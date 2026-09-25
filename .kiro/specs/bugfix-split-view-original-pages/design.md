# Bug Fix Design: Original PDF Copy Not Visible in Edit Mode

## Design Goal

Show the editor the real source PDF beside the converted HTML, for both new and existing documents, under both storage backends — and say so plainly when the original is genuinely unavailable.

## Architecture: three content tiers

The pane resolves content in order and stops at the first tier that renders:

| Tier | Content | Source | When |
|---|---|---|---|
| 1 | Embedded PDF (`<iframe>`, native browser viewer) | preserved `…-source.pdf` | Default for post-fix and repaired jobs |
| 2 | Page rasters (`<img>` list) | `…-page-N.png` | No PDF, PDF fails to load, or narrow viewport |
| 3 | `.split-source-empty` message | — | Nothing available |

Tier 2 is not redundant. Mobile browsers routinely refuse to render PDFs in an iframe and offer a download instead, and the split view has an explicit stacked layout at ≤820px (`document.html:262-270`). Tier 2 also covers pre-fix Docling jobs and any job whose source PDF is unrecoverable.

## Change map

| # | Change | File | Satisfies |
|---|---|---|---|
| 1 | Copy source PDF into the output dir | `python_app/convert.py` | FR-1 |
| 2 | Rasterise pages in the Surya path | `python_app/tools/extract_pdf.py`, `config.py` | FR-3 |
| 3 | Emit the asset descriptor | `python_app/tools/build_html.py` | FR-4 |
| 4 | Tiered pane + honest empty state | `python_app/templates/document.html` | FR-2, FR-5 |
| 5 | Exclude the preserved PDF from publish | `python_app/s3_publish.py` | FR-7 |
| 6 | Repair tool for existing jobs | `python_app/tools/repair_split_view.py` (new) | FR-6 |

The load-bearing trick, reused for both the PDF and the rasters: **put literal `images/…` strings in the HTML** so the existing rewrite at `convert.py:198-206` converts them to absolute media-bucket URLs for free. That is why the PDF lives under `images/` and why the descriptor holds relative paths.

---

## Change 1 — Preserve the source PDF

### Location and naming

`output/<job_dir>/images/<doc_stem>-source.pdf`

Under `images/` deliberately, for three consequences that need no new code:

- `convert.py:190-198` uploads the directory with `rglob("*")`, so the PDF reaches the media bucket.
- `oci_storage.upload_directory` skips only `.md` and `.json` (`oci_storage.py:184-186`), so `.pdf` is uploaded, and `mimetypes.guess_type` yields `application/pdf` (`oci_storage.py:155, 203`) — satisfying FR-2.4 with no content-type special-casing.
- `convert.py:200-206` rewrites the literal string `images/<stem>-source.pdf` in the HTML to the bucket URL.

It does **not** pollute the image list: `_collect_image_paths()` filters on image extensions only (`extract_pdf.py:437-448`), so `.pdf` is excluded and `image_count` is unchanged (FR-1.4).

### Implementation

In `convert.py`, after extraction and HTML build, **before** the OCI upload block at `:180`:

```python
    # Preserve the source PDF beside the output so the editor's split view can
    # show the real original, and so a converted document remains re-derivable.
    # Lives under images/ so the upload+rewrite block below carries it to the
    # bucket and turns its reference into an absolute URL, like any other asset.
    try:
        import shutil
        _src = Path(extraction.source_path)
        _preserved = Path(image_dir) / f"{_src.stem}-source.pdf"
        if _src.exists() and not _preserved.exists():
            shutil.copy2(_src, _preserved)
    except Exception as exc:
        print(f"[pdf2webview]   - Source PDF preservation warning: {exc}")
```

`extraction.source_path` is already part of the `ExtractionResult` contract (`extract_pdf.py:428`), so no signature changes.

### Retention

Unchanged (FR-1.6). `cleanup_job.py:58-81` keeps deleting transient uploads for terminal jobs; the durable copy now lives in the output directory, which already has the retention rules we want — expired only when old *and* unpublished, and never for published jobs (invariant I8, `cleanup_job.py:8-13, 92-96`). No edit to `cleanup_job.py`.

### Rejected alternatives

- **Stop deleting uploads** — weakens retention, leaves the original in a transient staging directory, and keeps the PDF outside the output the editor actually serves.
- **`output/<job>/source.pdf`** (outside `images/`) — cleaner-looking, but `convert.py` uploads only `images/`, so it would need new upload and rewrite code for no functional gain.

---

## Change 2 — Rasterise pages in the Surya path

### Config flag

In `config.py`, near the other feature flags:

```python
# Page rasters back the split-view fallback tier (used when the embedded PDF
# viewer can't render, e.g. narrow viewports). Disable to save storage.
SPLIT_VIEW_PAGE_RASTERS = _env_bool("SPLIT_VIEW_PAGE_RASTERS", True)
```

(Match the existing `_env_bool` helper; if absent, use the module's prevailing bool-parsing idiom.)

### Helper

New, beside the other PyMuPDF helpers (`extract_pdf.py:454`):

```python
# Render scale for split-view page rasters. 1.0 == 72 DPI; 1.75 ≈ 126 DPI —
# legible for side-by-side proofreading without bloating the output dir.
_PAGE_RASTER_ZOOM = 1.75


def _render_page_images(pdf_path: str, image_dir: Path, doc_stem: str) -> int:
    """Rasterise each PDF page to images/<doc_stem>-page-<N>.png (1-based).

    Mirrors the filenames the Docling path produces (extract_pdf.py:240-252),
    which the editor's split view uses as its fallback tier.

    Best-effort: returns the count written and never raises — a document
    without rasters must still convert (the pane falls back further).
    """
    import pymupdf

    written = 0
    doc = None
    try:
        doc = pymupdf.open(pdf_path)
        matrix = pymupdf.Matrix(_PAGE_RASTER_ZOOM, _PAGE_RASTER_ZOOM)
        for index, page in enumerate(doc, start=1):
            out_path = image_dir / f"{doc_stem}-page-{index}.png"
            if out_path.exists():
                written += 1
                continue                      # idempotent: reused by the repair tool
            try:
                page.get_pixmap(matrix=matrix).save(str(out_path))
                written += 1
            except Exception as exc:
                _log.warning("Could not rasterise page %d of %s: %s",
                             index, pdf_path, exc)
    except Exception as exc:
        _log.warning("Page rasterisation unavailable for %s: %s", pdf_path, exc)
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
    return written
```

`enumerate(doc, start=1)` because PyMuPDF indices are 0-based while the filename convention is 1-based. The `exists()` short-circuit makes the helper directly reusable by Change 6.

### Call site

Replace the placeholder report at `extract_pdf.py:423-428`:

```python
    report(
        "saving_pages",
        f"Saving {page_count} page image{'s' if page_count != 1 else ''}…",
        page_count=page_count,
        image_count=len(saved_names),
    )
    if getattr(_config, "SPLIT_VIEW_PAGE_RASTERS", True):
        _render_page_images(str(resolved_path), image_dir, doc_stem)
```

`_config` is already imported in this function (`extract_pdf.py:341`). Placement is after the `page_count` PyMuPDF fallback (`:411-419`) so the message carries a real number, and after `_collect_image_paths()` (`:408`) so rasters stay out of `image_paths` (FR-3.5). Docling path untouched (FR-8.4).

---

## Change 3 — Asset descriptor in the HTML

### Emitted markup

In `document.html`, immediately before the split-view `<script>` (`:8767`) — inside the region `_strip_editor_ui()` deletes, which runs from `<!-- Formula Input Dialog -->` to `</body>` (`s3_publish.py:186-190`), the same region the split-view script already relies on (`document.html:8761-8766`):

```html
  <!-- Editor-only: split-view source assets. Emitted as literal images/…
       paths so the conversion-time rewrite (convert.py) turns them into
       absolute bucket URLs. Stripped on publish with the block below. -->
  <script type="application/json" id="originalSplitAssets">
    {{ split_assets | tojson }}
  </script>
```

Shape:

```json
{"pdf": "images/02902739_ihga104-source.pdf",
 "pages": ["images/02902739_ihga104-page-1.png", "..."]}
```

`type="application/json"` keeps it inert — never executed, never fetched — and read via `textContent`. `| tojson` matters because autoescape is off here (`build_html.py:141-144`); it correctly encodes filenames with spaces or non-ASCII, e.g. `class 9 ch1-page-1.png`, removing the client's `encodeURIComponent` guesswork.

### Builder

In `build_html.py`:

```python
_PAGE_IMAGE_RE = re.compile(r"-page-(\d+)\.png$", re.IGNORECASE)


def _split_view_assets(image_paths, output_dir, doc_stem):
    """Split-view source assets as relative `images/...` refs.

    Derived from what exists on disk, so the editor never requests a missing
    asset. Literal `images/...` strings let convert.py rewrite them to bucket
    URLs (the same treatment the document's own <img> tags get).
    """
    pages = []
    for path in image_paths:
        name = Path(path).name
        match = _PAGE_IMAGE_RE.search(name)
        if match:
            pages.append((int(match.group(1)), f"images/{name}"))
    pdf_name = f"{doc_stem}-source.pdf"
    pdf_ref = (f"images/{pdf_name}"
               if (Path(output_dir) / "images" / pdf_name).exists() else None)
    return {"pdf": pdf_ref, "pages": [ref for _, ref in sorted(pages)]}
```

Passed into the existing `template.render(...)` call (`build_html.py:146-152`) as `split_assets=...`.

Sorting is numeric on the parsed page number, not lexicographic — otherwise `page-10` precedes `page-2` (FR-4.4).

**Ordering constraint:** the PDF must be copied in (Change 1) *before* `build_html` runs, or `pdf_ref` will be `None`. In `convert.py` the extraction step precedes the HTML build, so Change 1's copy block must sit with extraction rather than after the build. Concretely: place it immediately after extraction completes and before `build_interactive_html` is invoked, and keep it before the upload block. This is the one sequencing detail that will silently degrade Tier 1 to Tier 2 if got wrong, so it is called out in the tasks and verified explicitly.

---

## Change 4 — Tiered pane in the client

Rework `buildSourcePane()` (`document.html:8800-8846`). The toggle logic, CSS, rail-offset handling, and `MutationObserver` are untouched (FR-8.5).

### Asset resolution

```js
    // Preferred: the descriptor emitted by build_html.py — real filenames,
    // already rewritten to bucket URLs when serving from OCI.
    function getAssets() {
      var el = document.getElementById('originalSplitAssets');
      if (!el) return null;                       // pre-fix HTML → legacy guess
      try {
        var a = JSON.parse(el.textContent);
        return (a && typeof a === 'object') ? a : null;
      } catch (e) { return null; }
    }
```

`null` triggers the legacy path — `getJobDir()` + `getPageCount()` building `images/<jobDir>-page-<i>.png` — so unrepaired documents keep working (FR-8.2).

### Tier 1: embedded PDF

```js
    function buildPdfTier(container, url) {
      var frame = document.createElement('iframe');
      frame.className = 'split-source-pdf';
      frame.title = 'Original document';
      frame.src = url;
      container.appendChild(frame);
      return frame;
    }
```

Chosen over a bundled renderer because it delivers selectable text, search, and zoom with zero new dependency (FR-2.2). CSS addition — the only new rule, additive and scoped:

```css
    .split-source-pdf { width: 100%; height: 100%; min-height: 60vh; border: 0; display: block; }
```

**Viewport gate.** Narrow viewports skip Tier 1, since mobile browsers commonly download rather than render:

```js
      var preferPdf = assets.pdf && window.matchMedia('(min-width: 821px)').matches;
```

821px matches the existing stacked-layout breakpoint (`document.html:262`).

**Failure detection.** An `<iframe>` gives no reliable `onerror` for a 404 body, so a `HEAD` probe decides before committing:

```js
      fetch(assets.pdf, { method: 'HEAD' })
        .then(function (r) {
          if (r.ok && (r.headers.get('content-type') || '').indexOf('pdf') !== -1) {
            buildPdfTier(pages, assets.pdf);
          } else { buildPagesTier(pages, assets); }
        })
        .catch(function () { buildPagesTier(pages, assets); });
```

Same-origin under `local`; under `oci` the request follows a redirect to the public-read bucket, so a plain `HEAD` succeeds without CORS preflight (no custom headers, simple method).

### Tier 2 and Tier 3

Tier 2 keeps the current `<img>` list, with three changes:

- `img.src = urls[i]` straight from the descriptor (no re-encoding; already correct)
- `img.loading = (i === 0) ? 'eager' : 'lazy'` — page 1 loads eagerly so at least one image always settles (see below), the rest stay lazy (NFR-1.2)
- label parsed from the filename, `/-page-(\d+)\.png(?:[?#]|$)/i`, falling back to `i + 1`, so labels stay truthful when the descriptor has gaps

Load tracking replaces the silently-hiding handler:

```js
      var loaded = 0, failed = 0, firstSettled = false, messaged = false;

      function settle() {
        if (!messaged && loaded === 0 && failed >= 1 && firstSettled) {
          messaged = true;
          showEmpty(pages);
        }
      }
```

`onload` increments `loaded`; `onerror` hides the image and its label, increments `failed`, then calls `settle()`. Page 1's handlers set `firstSettled`.

Gating on page 1 rather than `failed === total` is deliberate: lazily loaded images below the fold may never fire either event until scrolled into view, so a `failed === total` condition would leave the message permanently unshown on a total failure. Since total failure almost always means "no files at all", page 1 is the reliable signal (FR-5.2), while partial failures show nothing because `loaded > 0` (FR-5.3).

`showEmpty(container)` is extracted from the existing inline markup and reused for the legacy `!jobDir || count < 1` branch (`:8818-8823`), keeping the current wording (FR-5.4).

---

## Change 5 — Keep the preserved PDF out of the learner bundle

`s3_publish.py` walks the job directory and uploads anything not in `SKIP_EXTENSIONS = {".html", ".htm", ".json", ".txt", ".md", ".log"}` (`:31`, `:107-110`). `.pdf` is absent, so the preserved source would be published to the learner media bucket.

**Do not add `.pdf` to `SKIP_EXTENSIONS`** — editor-attached PDFs are legitimate learner media, which the publish spec calls for explicitly (`publish-workflow/requirements.md` FR-3.1). A blanket skip would silently break them (FR-7.3).

Instead add a targeted predicate and consult it in the media-walk beside `_is_skip_file`:

```python
SOURCE_PDF_SUFFIX = "-source.pdf"


def _is_source_pdf(file_path):
    """The preserved original, kept for the editor's split view only.

    Editor-attached PDFs remain publishable; only this conversion artifact is
    withheld from the learner bundle.
    """
    return Path(file_path).name.lower().endswith(SOURCE_PDF_SUFFIX)
```

The split-view descriptor and script are already removed by `_strip_editor_ui()`'s Formula-Dialog-to-`</body>` deletion (FR-7.1), which Change 3's placement depends on and which verification checks rather than assumes.

---

## Change 6 — Repair tool for existing documents

New CLI: `python_app/tools/repair_split_view.py`.

```
python repair_split_view.py --all [--dry-run]
python repair_split_view.py --job 02902739 [--dry-run]
```

### Per-job algorithm

1. **Resolve** the output dir and its `*.html` (skip `.bak`).
2. **Skip** if the HTML already contains `id="originalSplitAssets"` (FR-6.8).
3. **Locate a source PDF**, in order (FR-6.2):
   - `output/<job>/images/<stem>-source.pdf` — already preserved
   - `uploads/<job_id>_*.pdf` — copy in via Change 1's logic
   - object storage, when `OUTPUT_BACKEND=oci` and `object_exists()` reports the key — download, then copy in
4. **Rasterise** missing pages with `_render_page_images()` when a PDF is now present (FR-6.3); its `exists()` short-circuit makes this cheap and idempotent.
5. **Upload** any newly added assets to the media bucket under `<job_dir>/`, reusing `oci_storage.upload_directory`, and capture the returned URL map.
6. **Patch the HTML** (FR-6.4):
   - back up to `<name>.html.bak` unless one exists, matching the convention already in `output/00e3441e_class 9 ch1/` (NFR-2.1)
   - replace the legacy split-view block — from `<!-- ===` preceding `Editor-only Split View` through the closing `</script>` of that IIFE — with the current template's version
   - insert the descriptor immediately before it, with paths rewritten to bucket URLs from step 5's map under `oci`, or left relative under `local`
7. **Re-upload the HTML** to the HTML bucket **only if the job is not published** (FR-6.6). This is the sharp edge: `s3_publish.py:1469` and `convert.py:216-217` both write `<job_dir>/<filename>`, so the published learner page and the editable copy share one object key. Re-uploading editor HTML for a published job would replace the learner page with editor chrome. Reuse `cleanup_job._is_published()`'s logic (`cleanup_job.py:34-37`) rather than reimplementing the check. For published jobs: patch the local copy, report `patched-local-only`, and leave the bucket alone.
8. **Report** one line per job: `repaired-pdf`, `repaired-rasters`, `patched-local-only`, `no-source-found`, `skipped-current`, or `error`.

### Extracting the script block without duplicating it

The replacement JS must not be a second copy of the template's script, or the two drift. The tool renders the block from the template instead:

- Read `python_app/templates/document.html`.
- Slice from the `Editor-only Split View` banner comment to the end of that `<script>`.
- Use that slice as the replacement, since the block is self-contained — it reads only the DOM and the descriptor, and takes no Jinja variables.

To make future runs deterministic rather than comment-shaped, the template also gains stable markers around the region:

```html
  <!-- split-view:begin -->
  … descriptor + script …
  <!-- split-view:end -->
```

The tool matches the markers when present and falls back to the legacy banner-comment pattern for pre-fix HTML. The markers sit inside the stripped region, so they never reach learners.

### Expected outcome for the reported document

`02902739_ihga104`'s source PDF is absent from `uploads/` (verified: no `*ihga*`), so unless it exists in the bucket the tool reports `no-source-found` and patches the HTML so the pane shows the FR-5 message (FR-6.5). Restoring its original pane needs the source PDF re-uploaded and the job reconverted. The tool makes that state legible instead of silently blank.

---

## Files Changed

| File | Change |
|---|---|
| `python_app/convert.py` | Copy the source PDF into `images/` before the HTML build and the upload block |
| `python_app/config.py` | `SPLIT_VIEW_PAGE_RASTERS` flag |
| `python_app/tools/extract_pdf.py` | `_PAGE_RASTER_ZOOM`, `_render_page_images()`; call it from `_extract_via_surya()`; fix the `saving_pages` message |
| `python_app/tools/build_html.py` | `_PAGE_IMAGE_RE`, `_split_view_assets()`; pass `split_assets=` to `template.render()` |
| `python_app/templates/document.html` | `#originalSplitAssets` descriptor; `split-view:begin/end` markers; tiered `buildSourcePane()`; one `.split-source-pdf` CSS rule |
| `python_app/s3_publish.py` | `SOURCE_PDF_SUFFIX`, `_is_source_pdf()`, consulted in the media walk |
| `python_app/tools/repair_split_view.py` | **New.** Backfill/repair CLI |

**Deliberately unchanged:** `oci_storage.py`, `storage/output_store.py`, `app.py`, `cleanup_job.py`, existing split-view CSS (`document.html:199-267`), `#railSplitBtn` (`:3428`), `SKIP_EXTENSIONS`.

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| **Repair tool overwrites published learner HTML** | **High** | Publish and conversion share the HTML-bucket key; tool re-uploads only for unpublished jobs, reusing `_is_published()`. `--dry-run` first; AC-11 verifies a published page afterwards |
| Repair tool corrupts baked HTML via regex | Medium-High | `.html.bak` backup before every patch; idempotence guard; `--dry-run`; verify on a copied job before `--all` |
| Preserved PDF publicly fetchable from the media bucket | Medium | Documented (FR-7.4). Comparable to the page rasters already public there, which reproduce the same content. Withheld from the learner bundle so it is at least unlisted. PAR/expiring URLs noted as follow-up |
| Native PDF viewer blocked or unavailable in iframe | Medium | HEAD probe before committing; automatic Tier 2 fallback; narrow viewports skip Tier 1 outright |
| PDF copied after `build_html` runs → `pdf` always `null` | Medium | Explicit ordering constraint in Change 3; task step and AC-2 verify the descriptor actually carries a PDF ref |
| Descriptor's `tojson` output breaks the `convert.py` replace | Medium | JSON escapes `\` and `"`; `images/<name>` stays literal for ordinary names. Verified against the space-containing `class 9 ch1` case |
| Storage growth (PDF + rasters per job) | Medium | `SPLIT_VIEW_PAGE_RASTERS` flag; measured in verification (NFR-1.3) rather than assumed |
| Blanket `.pdf` publish skip would break attached PDF media | Medium | Explicitly rejected; targeted `-source.pdf` predicate instead, with AC-13 covering both directions |
| Lazy loading suppresses the empty state | Low-Medium | Page 1 eager and gating the message |
| PyMuPDF fails on a malformed PDF | Low | Helper never raises; conversion proceeds; pane falls through the tiers |

## Verification Plan

1. **Helpers in isolation:** build a 3-page PDF with `pymupdf.open()` + `new_page()` (the pattern at `python_app/tests/test_surya_ocr.py:27-28`); assert `_render_page_images()` writes 3 correctly named files, is idempotent on a second call, and returns `0` without raising for a nonexistent path. Assert `_split_view_assets()` orders `page-2` before `page-10`, excludes figure images, and sets `pdf` only when the file exists.
2. **Local happy path:** convert a multi-page PDF with `OCR_ENGINE=surya`. Confirm the preserved PDF and N rasters on disk, and that the emitted descriptor's `pdf` is non-null (AC-1, and the Change 3 ordering constraint).
3. **Tier 1:** open in edit mode, toggle split view, confirm the real PDF renders with selectable text, working search, and zoom (AC-2).
4. **Tier 2:** delete the preserved PDF, reload, confirm fallback to labelled rasters (AC-3). Then remove `-page-3.png` only and confirm partial behavior (AC-5).
5. **Tier 3:** remove PDF and all rasters, confirm the message (AC-4).
6. **Measurements:** record added conversion time and the on-disk size of PDF + rasters (NFR-1.1, NFR-1.3). Re-run with `SPLIT_VIEW_PAGE_RASTERS=false` and confirm rasters are skipped while Tier 1 still works.
7. **Naming edge case:** convert a PDF whose filename contains a space; confirm descriptor entries and a working pane (`class 9 ch1` class of names).
8. **Regression:** open `00e3441e_class 9 ch1` and confirm its split view still works via the legacy path, with its baked HTML untouched (AC-7).
9. **OCI:** with `OUTPUT_BACKEND=oci`, convert and inspect the saved HTML — descriptor entries must be absolute media-bucket URLs for both PDF and rasters, and both must load. Confirm the PDF is served as `application/pdf` (AC-6).
10. **Publish:** publish a post-fix document containing both the preserved PDF and an editor-attached PDF. Grep the learner HTML for `originalSplitAssets`, `railSplitBtn`, `split-source`, `split-view`, and `-source.pdf` — all absent. Confirm the attached PDF *did* reach the media bucket and the preserved one did not (AC-13).
11. **Repair, dry run:** `--all --dry-run`, inspect the report, and confirm nothing changed on disk — compare a checksum manifest of `output/` before and after (AC-8).
12. **Repair, recoverable job:** pick a job whose upload PDF still exists, run `--job`, confirm a working PDF-backed split view (AC-9).
13. **Repair, unrecoverable job:** run against `02902739_ihga104`, confirm `no-source-found`, the HTML patched to show the message, and a `.html.bak` created (AC-10).
14. **Repair, published safety:** after a full run, open a published learner page and confirm it still has no editor chrome; confirm the tool reported `patched-local-only` for published jobs (AC-11).
15. **Repair, idempotence:** re-run `--all`; every job reports `skipped-current` and no writes occur (AC-12).
16. **Header edit:** change the title and the "N pages · M images" text in edit mode, save, reload, toggle split view — pane still works, proving the `.document-meta` scrape is gone (AC-14).
17. **Test suite:** run `python_app/tests/`, particularly `test_surya_ocr.py` and `test_oci_output_store.py`.
