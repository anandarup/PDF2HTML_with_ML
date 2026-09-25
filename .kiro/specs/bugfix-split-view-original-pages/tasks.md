# Bug Fix Tasks: Original PDF Copy Not Visible in Edit Mode

Ordered so each task is independently verifiable. Tasks 1–4 are backend and testable without a browser. Task 5 is the client. Task 6 keeps the learner bundle clean. Task 7 is the repair tool — the highest-risk item, deliberately last so it can reuse verified code. Task 8 is end-to-end verification.

---

## Task 1: Preserve the source PDF with the output

**Files:** `python_app/convert.py`
**Satisfies:** FR-1

- [ ] 1.1: Locate the conversion sequence in `convert.py` — extraction (`~:94-130`), HTML build, then the OCI upload block (`:180`).
- [ ] 1.2: Insert a copy step that writes the source PDF to `Path(image_dir) / f"{Path(extraction.source_path).stem}-source.pdf"`:
  - use `shutil.copy2`
  - skip if the destination already exists (idempotent)
  - wrap in `try/except`, print a warning, never fail the conversion (FR-1.5)
  - comment why it lives under `images/`: the existing `upload_directory` + URL-rewrite block carries it to the bucket and absolutises its reference for free
- [ ] 1.3: **Critical ordering — place the copy after extraction but BEFORE `build_interactive_html` is called**, and before the upload block. If it lands after the HTML build, `_split_view_assets()` (Task 3) will not see the file and every document silently degrades to Tier 2. This is the single most likely way to half-break this fix.
- [ ] 1.4: Confirm `extraction.source_path` is populated on the Surya path (`extract_pdf.py:428`) and the Docling path.
- [ ] 1.5: Make no change to `cleanup_job.py` (FR-1.6).

**Verify:** convert any PDF; assert `output/<job>/images/<stem>-source.pdf` exists, is byte-identical to the input (`cmp`), and that `image_count` in the rendered header is unchanged from before the fix (FR-1.4 — `.pdf` is excluded by `_collect_image_paths`).

---

## Task 2: Rasterise pages in the Surya extraction path

**Files:** `python_app/config.py`, `python_app/tools/extract_pdf.py`
**Satisfies:** FR-3

- [ ] 2.1: Add `SPLIT_VIEW_PAGE_RASTERS` to `config.py`, defaulting to `True`, using the module's existing bool-env idiom. Comment that rasters back the split-view fallback tier and can be disabled to save storage.
- [ ] 2.2: Add `_PAGE_RASTER_ZOOM = 1.75` to `extract_pdf.py` with a comment noting `1.0 == 72 DPI`, so ~126 DPI (NFR-1.1).
- [ ] 2.3: Add `_render_page_images(pdf_path, image_dir, doc_stem) -> int` near `_get_first_page_text` (`:454`):
  - local `import pymupdf`, matching the style at `:415`, `:456`, `:488`, `:550`
  - iterate `enumerate(doc, start=1)`; write `f"{doc_stem}-page-{index}.png"` via `page.get_pixmap(matrix=pymupdf.Matrix(z, z)).save(...)`
  - **skip and count pages whose file already exists** — makes it idempotent and directly reusable by Task 7
  - per-page `try/except` → log warning, continue (FR-3.6)
  - outer `try/except` → log warning, return `0`; never raise
  - close the doc in `finally`
  - docstring referencing the Docling convention at `:240-252`
- [ ] 2.4: In `_extract_via_surya()`, replace the placeholder report at `:423-428` — message becomes `f"Saving {page_count} page image{'s' if page_count != 1 else ''}…"` instead of `"Text extraction complete."` (FR-3.7).
- [ ] 2.5: Immediately after, call `_render_page_images(str(resolved_path), image_dir, doc_stem)` guarded by `getattr(_config, "SPLIT_VIEW_PAGE_RASTERS", True)` (`_config` already imported at `:341`).
- [ ] 2.6: Confirm placement is after the `page_count` fallback (`:411-419`) and after `_collect_image_paths()` (`:408`), so the message is accurate and rasters stay out of `image_paths` (FR-3.5).
- [ ] 2.7: Leave the Docling path (`:240-252`) untouched (FR-8.4).

**Verify:** in a shell, build a 3-page PDF with `pymupdf.open()` + `new_page()`, call `_render_page_images()` on a temp dir → 3 non-empty files correctly named; call again → returns 3, writes nothing (check mtimes); call with a bogus path → returns `0`, no exception.

---

## Task 3: Emit the split-view asset descriptor

**Files:** `python_app/tools/build_html.py`
**Satisfies:** FR-4

- [ ] 3.1: Add `_PAGE_IMAGE_RE = re.compile(r"-page-(\d+)\.png$", re.IGNORECASE)` at module level (confirm `re` and `Path` imports exist; add if missing).
- [ ] 3.2: Add `_split_view_assets(image_paths, output_dir, doc_stem) -> dict` returning `{"pdf": <ref or None>, "pages": [...]}`:
  - page refs from `image_paths` matching `_PAGE_IMAGE_RE`, as `f"images/{name}"`
  - sorted **numerically** by parsed page number so `page-2` precedes `page-10` (FR-4.4)
  - `pdf` set to `f"images/{doc_stem}-source.pdf"` only when that file exists on disk, else `None` (FR-4.3)
  - docstring: literal `images/…` strings are what let `convert.py:198-206` rewrite them to bucket URLs
- [ ] 3.3: Pass `split_assets=_split_view_assets(...)` into the `template.render(...)` call at `:146-152`. Confirm the function has access to the output dir and doc stem; derive them from the existing output path variable rather than adding parameters if possible.

**Verify:** call `_split_view_assets()` with a shuffled list containing `x-page-10.png`, `x-page-2.png`, `x-page-1.png`, `x-figure-3.png`, `abc_img.jpg` → `pages` is exactly `["images/x-page-1.png", "images/x-page-2.png", "images/x-page-10.png"]`. With no source PDF on disk → `pdf` is `None`. With one present → `pdf` is set.

---

## Task 4: Descriptor markup and strip markers in the template

**Files:** `python_app/templates/document.html`
**Satisfies:** FR-4.1, FR-4.6, FR-6.4 (enables the repair tool)

- [ ] 4.1: Immediately before the split-view `<script>` (`:8767`), add:
  ```html
  <script type="application/json" id="originalSplitAssets">{{ split_assets | tojson }}</script>
  ```
  with a comment explaining it is editor-only and why the paths are literal.
- [ ] 4.2: Wrap the descriptor **and** the split-view script in stable markers `<!-- split-view:begin -->` / `<!-- split-view:end -->` so Task 7 can locate the region deterministically instead of pattern-matching prose comments.
- [ ] 4.3: Confirm both sit **after** the `<!-- Formula Input Dialog -->` comment, inside the region `_strip_editor_ui()` deletes (`s3_publish.py:186-190`), so they never reach learners (FR-4.6).
- [ ] 4.4: Use `| tojson` rather than manual quoting — autoescape is off (`build_html.py:141-144`) and filenames may contain spaces or non-ASCII.

**Verify:** render a document via `build_html.py`; grep the output for `originalSplitAssets`, `split-view:begin`, and an `images/…-page-1.png` entry. Confirm the JSON parses.

---

## Task 5: Tiered split-view pane in the client

**Files:** `python_app/templates/document.html`
**Satisfies:** FR-2, FR-5, FR-8.2

- [ ] 5.1: Add one CSS rule beside the existing split-view block (`:199-267`), additive only:
  ```css
  .split-source-pdf { width: 100%; height: 100%; min-height: 60vh; border: 0; display: block; }
  ```
- [ ] 5.2: Add `getAssets()` — read `#originalSplitAssets`, `JSON.parse` its `textContent`, return the object; return `null` on missing element or parse error.
- [ ] 5.3: Extract `showEmpty(container)` from the existing inline empty-state markup, keeping the current wording; reuse it for the legacy `!jobDir || count < 1` branch (`:8818-8823`) (FR-5.4).
- [ ] 5.4: Add `buildPdfTier(container, url)` creating an `<iframe class="split-source-pdf" title="Original document">` (FR-2.1–FR-2.3).
- [ ] 5.5: Add `buildPagesTier(container, assets)` holding the reworked `<img>` list:
  - `img.src` taken straight from the descriptor, no re-encoding
  - `img.loading = (i === 0) ? 'eager' : 'lazy'` (NFR-1.2 plus a reliable settle signal)
  - label parsed via `/-page-(\d+)\.png(?:[?#]|$)/i`, falling back to `i + 1`
- [ ] 5.6: Add load tracking in the pages tier — `loaded`, `failed`, `firstSettled`, `messaged`; `onload` increments `loaded`; `onerror` hides image + label, increments `failed`, calls `settle()`; page 1's handlers set `firstSettled`.
- [ ] 5.7: Implement `settle()` → call `showEmpty()` only when `!messaged && loaded === 0 && failed >= 1 && firstSettled` (FR-5.2, FR-5.3). Do not gate on `failed === total`: lazy images below the fold may never fire, so the message would never appear on a total failure.
- [ ] 5.8: Rewrite `buildSourcePane()` as the tier selector:
  - `var assets = getAssets();`
  - `assets === null` → legacy path (`getJobDir()` + `getPageCount()`, guessed filenames) so unrepaired documents keep working (FR-8.2)
  - `preferPdf = assets.pdf && window.matchMedia('(min-width: 821px)').matches` — 821px matches the existing stacked breakpoint (`:262`)
  - when `preferPdf`, `fetch(assets.pdf, {method:'HEAD'})`; on `r.ok` **and** a `content-type` containing `pdf` → `buildPdfTier`, else → `buildPagesTier`; `.catch` → `buildPagesTier`
  - no PDF → `buildPagesTier`; empty `pages` too → `showEmpty`
- [ ] 5.9: Leave untouched: `enableSplit`/`disableSplit`/`setRailOffset`, the `MutationObserver`, `#railSplitBtn` (`:3428`), and all pre-existing CSS (FR-8.5).

**Verify:** covered by Task 8 steps 3–5; at this point just confirm no JS console errors on load and that toggling split view still opens and closes the pane.

---

## Task 6: Keep the preserved PDF out of the learner bundle

**Files:** `python_app/s3_publish.py`
**Satisfies:** FR-7

- [ ] 6.1: Add `SOURCE_PDF_SUFFIX = "-source.pdf"` near `SKIP_EXTENSIONS` (`:31`).
- [ ] 6.2: Add `_is_source_pdf(file_path)` matching on the filename suffix, case-insensitively, beside `_is_skip_file` (`:107-110`).
- [ ] 6.3: Consult it in the media walk in `publish_document` (around `:1406`), skipping the file the same way `_is_skip_file` results are skipped.
- [ ] 6.4: **Do not add `.pdf` to `SKIP_EXTENSIONS`** — editor-attached PDFs are legitimate learner media per the publish spec's FR-3.1, and a blanket skip would silently break them (FR-7.3). Add a comment saying so, so a later reader does not "simplify" it.
- [ ] 6.5: Confirm `_strip_editor_ui()` already removes the descriptor and script by virtue of Task 4.3's placement; add no new strip rules unless verification shows a gap.

**Verify:** covered by Task 8 step 10, which checks both directions — preserved PDF withheld, attached PDF still published.

---

## Task 7: Repair tool for existing documents

**Files:** `python_app/tools/repair_split_view.py` (new)
**Satisfies:** FR-6
**Highest-risk task.** It writes to every job's HTML. Build it after Tasks 1–5 are verified, and exercise it on a copied job directory before any `--all` run.

- [ ] 7.1: CLI scaffold with `argparse`: `--job <id>`, `--all`, `--dry-run`. Require exactly one of `--job`/`--all`. Import `config`, and `build_job_store` for publish status.
- [ ] 7.2: Job enumeration — resolve output dirs under `config.OUTPUT_DIR`; find each job's `*.html`, excluding `*.bak`.
- [ ] 7.3: Idempotence guard — skip and report `skipped-current` when the HTML already contains `id="originalSplitAssets"` (FR-6.8).
- [ ] 7.4: Source-PDF resolution in order (FR-6.2): output-dir copy → `uploads/<job_id>_*.pdf` → object storage via `oci_storage.object_exists()`/`get_object()` when `OUTPUT_BACKEND=oci`. Copy any external find into `images/<stem>-source.pdf`, reusing Task 1's logic (extract it into a shared helper rather than duplicating).
- [ ] 7.5: Raster generation — when a PDF is present, call `_render_page_images()` from Task 2 for missing pages (FR-6.3). Its `exists()` short-circuit keeps this cheap.
- [ ] 7.6: Asset upload — under `oci`, upload newly added files with `oci_storage.upload_directory(images_dir, f"{job_dir}/")` and keep the returned URL map for path rewriting.
- [ ] 7.7: HTML patching (FR-6.4):
  - back up to `<name>.html.bak` unless one already exists (NFR-2.1)
  - build the replacement region by slicing the current `document.html` between `split-view:begin`/`split-view:end`; fall back to slicing from the `Editor-only Split View` banner comment through that IIFE's closing `</script>` for pre-fix documents
  - substitute the descriptor's `{{ split_assets | tojson }}` placeholder with the concrete JSON for this job — absolute bucket URLs from step 7.6 under `oci`, relative `images/…` under `local`
  - replace the legacy region in the document with the new region
- [ ] 7.8: **Publish-safety gate (FR-6.6).** Re-upload the patched HTML to the HTML bucket **only when the job is not published**. Reuse `cleanup_job._is_published()` (`cleanup_job.py:34-37`) rather than reimplementing it. Publishing and conversion write the *same* object key `<job_dir>/<filename>` (`s3_publish.py:1469`, `convert.py:216-217`), so re-uploading editor HTML for a published job would overwrite the learner page with editor chrome. For published jobs, patch the local copy only and report `patched-local-only`.
- [ ] 7.9: `--dry-run` — log every intended action (copy, rasterise, upload, patch, re-upload) and perform no writes at all, including no `.bak` creation (AC-8).
- [ ] 7.10: Per-job reporting with outcomes `repaired-pdf`, `repaired-rasters`, `patched-local-only`, `no-source-found`, `skipped-current`, `error`, plus a summary count. Errors on one job must not abort the run.
- [ ] 7.11: Module docstring covering usage, the published-key hazard, and the `.bak` recovery path.

**Verify:** Task 8 steps 11–15.

---

## Task 8: End-to-end verification

- [ ] 8.1: **Helpers** — `_render_page_images()` on a 3-page generated PDF: 3 files, idempotent on re-run, `0` and no raise on a bad path. `_split_view_assets()`: numeric ordering, figures excluded, `pdf` present only when the file is.
- [ ] 8.2: **Local conversion** — convert a multi-page PDF with `OCR_ENGINE=surya`. Assert the preserved PDF and N rasters exist, and that the emitted descriptor's `pdf` is **non-null** (AC-1; also proves Task 1.3's ordering is right).
- [ ] 8.3: **Tier 1** — edit mode → split view → the real PDF renders; confirm text selection, in-document search, and zoom (AC-2).
- [ ] 8.4: **Tier 2** — delete the preserved PDF, reload, confirm fallback to labelled rasters (AC-3). Then delete only `-page-3.png`: others still render, page 3 hidden, no message (AC-5).
- [ ] 8.5: **Tier 3** — remove the PDF and all rasters, confirm the "not available" message, never a blank pane (AC-4).
- [ ] 8.6: **Measurements** — record added conversion time and combined on-disk size of PDF + rasters (NFR-1.1, NFR-1.3). Re-run with `SPLIT_VIEW_PAGE_RASTERS=false`: no rasters written, Tier 1 still works.
- [ ] 8.7: **Naming edge case** — convert a PDF whose filename contains a space; confirm descriptor entries and a working pane (the `class 9 ch1` name class, and the `tojson`-vs-rewrite risk).
- [ ] 8.8: **Regression** — open `00e3441e_class 9 ch1`, confirm split view still works through the legacy path with its baked HTML untouched (AC-7).
- [ ] 8.9: **OCI** — with `OUTPUT_BACKEND=oci`, convert and inspect the saved HTML: descriptor entries are absolute media-bucket URLs for both PDF and rasters; both load in the browser; the PDF is served as `application/pdf` (AC-6).
- [ ] 8.10: **Publish** — publish a post-fix document that contains both the preserved PDF and an editor-attached PDF. Learner HTML must contain none of `originalSplitAssets`, `railSplitBtn`, `split-source`, `split-view`, `-source.pdf`. Confirm the attached PDF reached the media bucket and the preserved one did not (AC-13).
- [ ] 8.11: **Repair dry run** — take a checksum manifest of `output/`, run `--all --dry-run`, inspect the report, re-take the manifest and confirm it is identical (AC-8).
- [ ] 8.12: **Repair, recoverable job** — pick a job whose `uploads/` PDF still exists, run `--job`, confirm a working PDF-backed split view (AC-9).
- [ ] 8.13: **Repair, unrecoverable job** — run against `02902739_ihga104`: reports `no-source-found`, patches the HTML to show the message, creates `.html.bak` (AC-10).
- [ ] 8.14: **Repair, published safety** — after a full run, open a published learner page and confirm it still has no editor chrome; confirm published jobs reported `patched-local-only` (AC-11). This is the highest-consequence check in the plan.
- [ ] 8.15: **Repair idempotence** — re-run `--all`: every job reports `skipped-current`, no writes (AC-12).
- [ ] 8.16: **Header edit** — change the title and the "N pages · M images" text, save, reload, toggle split view; pane still works, proving the `.document-meta` scrape is gone (AC-14).
- [ ] 8.17: **Test suite** — run `python_app/tests/`, particularly `test_surya_ocr.py` and `test_oci_output_store.py`.

---

## Suggested Sequencing

Tasks 1–5 are shippable on their own and fix all future conversions. Task 6 must ship with them, since Task 1 starts producing a PDF that publish would otherwise upload. Task 7 can follow as a separate change once 1–6 are verified in place — it depends on their code and is the only task that touches existing documents.

## Definition of Done

- Tasks 1–7 implemented, Task 8 fully verified.
- All 14 acceptance criteria in `requirements.md` pass.
- No changes to `oci_storage.py`, `storage/output_store.py`, `app.py`, `cleanup_job.py`, `SKIP_EXTENSIONS`, or the pre-existing split-view CSS.
- A published learner page is confirmed intact after a full repair run.
