# Bug Fix Requirements: Original PDF Copy Not Visible in Edit Mode

## Bug Summary

In edit mode, toggling the split-view button (`#railSplitBtn`, "Split view: compare with original pages") opens the right-hand "Original" pane, but the pane renders as a blank/white panel instead of showing the original PDF.

**Reported on:** `https://poc-interactivetxtbk.diksha.gov.in/output/02902739_ihga104/02902739_ihga104.html`
(document header reads `22 pages · 40 images`)

## Expected vs Actual

| | Behavior |
|---|---|
| **Expected** | The right pane shows the original PDF so the editor can proofread the converted HTML against the source. |
| **Actual** | The pane header ("ORIGINAL") and its chrome render, but the content area is completely empty. No pages, no labels, and no explanatory message. |

## Scope Decision

The original design rasterises pages to PNG and shows them as an image list. This revision keeps that as a fallback but makes **the real source PDF the primary view**, and adds retention plus a repair path so existing documents can be recovered. Three linked changes, agreed with the requester:

1. **Repair existing documents** — a tool that retrofits already-converted jobs rather than only fixing future conversions.
2. **Show the real PDF** — an embedded viewer with selectable text, search, and zoom, instead of flat page images.
3. **Preserve the source PDF** — so a converted document can always be re-derived and re-proofread against its original.

## Root Cause

### RC-1: the active OCR path never rasterises page images

The split-view client requests `images/<job_dir>-page-<N>.png` (`python_app/templates/document.html:8829`). Only the Docling path writes those files (`python_app/tools/extract_pdf.py:240-252`). `_extract_via_surya()` (`:321-434`) — the active path, since `.env` sets `OCR_ENGINE=surya` — saves only API-returned figure images (`<hash>_img.jpg`) and reports `saving_pages` with "Text extraction complete." **without writing a single page image**.

Verified on disk:
- `output/02902739_ihga104/images/` — 41 entries: one `.md` + 40 `<hash>_img.jpg`; `ls | grep -c page` → **0**
- `output/00e3441e_class 9 ch1/images/` (Docling-era) — 15 `…-page-N.png` present; split view works there

**Every job converted since `OCR_ENGINE=surya` became active is affected.**

### RC-2: the failure is silent

`img.onerror` hides the failed image *and* its page label (`document.html:8836-8838`). The `.split-source-empty` message only appears when `!jobDir || count < 1` (`:8818-8823`). A document with a valid page count and zero available images therefore yields a correctly sized, entirely empty pane. CSS gives the pane `display:block; width:50%; height:100vh` (`:214-222`), which is why it reads as a blank white panel rather than collapsing.

### RC-3: runtime-built image URLs are never rewritten for OCI

`convert.py:198-206` uploads `images/` to the media bucket and rewrites literal `images/<rel>` strings in the HTML to absolute bucket URLs. The pane builds its URLs **in JavaScript at runtime**, so those strings don't exist in the HTML and are never rewritten. The relative URL resolves to `/output/<job>/images/…`, which `OciOutputStore.serve()` (`storage/output_store.py:141-166`) redirects to the **HTML bucket** — but images live in the **media bucket**. Generating rasters alone would still leave the pane blank under `OUTPUT_BACKEND=oci`.

### RC-4: page count and filenames are guessed from rendered text

- `getPageCount()` scrapes `/(\d+)\s*page/i` from `.document-meta` (`:8791-8798`), which is editable content (`:3251-3254`).
- `img.src` assumes `job_dir == PDF filename stem`. True today, but any divergence silently 404s everything.

### RC-5: the source PDF is never retained

The upload lives at `uploads/<job_id>_<name>.pdf` and is deleted once the job reaches a terminal state (`cleanup_job.py:58-81`). It is never copied into the output directory. `uploads/` currently holds 110 PDFs, none for `ihga104`. So today a converted document cannot be compared against, or re-derived from, its original — and no PDF-based viewer is possible.

## Functional Requirements

### FR-1: Preserve the source PDF with the output

- **FR-1.1:** At conversion, the source PDF SHALL be copied into the job's output directory, alongside the other per-job assets, before the storage upload step runs.
- **FR-1.2:** The copy SHALL be named deterministically from the document stem so the viewer can address it without guessing.
- **FR-1.3:** The copy SHALL be placed so that the existing `convert.py` upload and URL-rewrite logic carries it to object storage and rewrites its reference to an absolute bucket URL, with no change to the upload code (satisfying RC-3 for the PDF too).
- **FR-1.4:** The preserved PDF SHALL NOT be counted as an extracted image, SHALL NOT appear in `image_paths`, and SHALL NOT change the `image_count` shown in the document header.
- **FR-1.5:** A failed copy SHALL be logged and SHALL NOT fail the conversion.
- **FR-1.6:** `cleanup_job.py`'s existing behavior SHALL NOT change. Uploads stay transient; the durable copy lives with the output and inherits output retention, including the "published content is never deleted" invariant (I8).
- **FR-1.7:** The preserved PDF SHALL NOT be uploaded or referenced by the published learner HTML (see FR-7).

### FR-2: Embedded PDF viewer as the primary pane content

- **FR-2.1:** When a preserved source PDF is available, the split-view pane SHALL display it in an embedded viewer.
- **FR-2.2:** The viewer SHALL support text selection, in-document search, and zoom. Using the browser's native PDF viewer via an iframe is acceptable and preferred over bundling a JS PDF renderer, since the application already relies on CDN-hosted viewers for other media.
- **FR-2.3:** The viewer SHALL fill the pane's available height and scroll independently of the converted-HTML pane, matching current pane behavior.
- **FR-2.4:** The PDF SHALL be served with `Content-Type: application/pdf` so browsers render rather than download it.
- **FR-2.5:** When the viewer cannot render (no PDF available, load failure, or a viewport where embedded PDFs are unreliable), the pane SHALL fall back to page rasters per FR-3, and then to the empty state per FR-5.

### FR-3: Page rasters as the fallback tier

- **FR-3.1:** `_extract_via_surya()` SHALL write one page raster per PDF page into the job's `images/` directory.
- **FR-3.2:** Filenames SHALL follow the existing convention `<doc_stem>-page-<N>.png`, `N` 1-based, matching the Docling path exactly.
- **FR-3.3:** Rasterisation SHALL use PyMuPDF, already a dependency and already imported in this module (`extract_pdf.py:415, 456, 488, 550`). No new dependency SHALL be added.
- **FR-3.4:** Rasterisation SHALL be controllable by configuration so operators can trade storage against fallback robustness. It SHALL default to enabled.
- **FR-3.5:** Rasterisation SHALL NOT change the extracted markdown, the figure images, or the reported `image_count`. Page rasters are a viewer asset, not extracted content.
- **FR-3.6:** Rasterisation failure SHALL be logged and SHALL NOT fail the job.
- **FR-3.7:** The `saving_pages` progress report SHALL reflect real work instead of "Text extraction complete."

### FR-4: Explicit asset descriptor in the HTML

- **FR-4.1:** `build_html.py` SHALL emit the split-view asset references — the PDF path and the page-raster paths — into the rendered HTML as literal `images/…` strings, rather than leaving the client to guess filenames.
- **FR-4.2:** The descriptor SHALL be machine-readable by the split-view script and SHALL survive the conversion-time OCI rewrite (`convert.py:198-206`), satisfying RC-3 with no upload-code change.
- **FR-4.3:** The descriptor SHALL reference only assets that exist on disk at build time.
- **FR-4.4:** Page-raster references SHALL be ordered numerically, so `page-2` precedes `page-10`.
- **FR-4.5:** The split-view script SHALL prefer the descriptor and SHALL NOT scrape `.document-meta` when it is present (satisfying RC-4).
- **FR-4.6:** The descriptor SHALL be removed from the published learner HTML, consistent with the rest of the editor-only split-view markup.

### FR-5: Honest empty state

- **FR-5.1:** The pane SHALL track whether any content tier rendered successfully.
- **FR-5.2:** When no tier renders, the pane SHALL display the `.split-source-empty` message instead of an empty area.
- **FR-5.3:** When some page rasters load and others fail, the loaded ones SHALL remain visible and correctly labelled; only failed entries are hidden and no message is shown.
- **FR-5.4:** Messages SHALL be non-technical and SHALL NOT expose URLs, bucket names, or stack traces.

### FR-6: Repair existing documents

- **FR-6.1:** A repair tool SHALL retrofit already-converted jobs, operating on a single job or on all jobs.
- **FR-6.2:** For each job it SHALL locate a source PDF, preferring the output-directory copy, then the transient upload, then object storage. When one is found outside the output directory it SHALL be copied in and uploaded, per FR-1.
- **FR-6.3:** When a source PDF is available it SHALL generate any missing page rasters.
- **FR-6.4:** It SHALL patch the document's baked-in split-view script and inject the FR-4 descriptor, because **each generated HTML embeds its own copy of that script** and `serve_output` returns the baked file verbatim (`app.py:685-698`) — documents are never re-rendered through the template.
- **FR-6.5:** When no source PDF can be found anywhere, the tool SHALL still patch the HTML so the document shows the FR-5 message instead of a blank pane.
- **FR-6.6:** The tool SHALL treat the local output copy as the authoritative editable HTML and SHALL NOT overwrite published learner content. Publishing writes the stripped learner HTML to the **same** HTML-bucket key that conversion used for the editable copy (`s3_publish.py:1469` and `convert.py:216-217` both resolve to `<job_dir>/<filename>`), so re-uploading editor HTML for a published job would replace the learner page with editor chrome. The tool SHALL re-upload only for jobs that are not published.
- **FR-6.7:** The tool SHALL support `--dry-run`, SHALL be idempotent, and SHALL report per-job outcomes.
- **FR-6.8:** The tool SHALL skip documents already carrying the new descriptor, so repeated runs are cheap.

### FR-7: Learner view and exposure control

- **FR-7.1:** The published learner HTML SHALL contain no split-view markup, script, descriptor, or reference to the source PDF.
- **FR-7.2:** `s3_publish` SHALL NOT upload the preserved source PDF to the learner media bucket.
- **FR-7.3:** The exclusion SHALL target the preserved source PDF specifically, **not** all PDFs. Editor-attached PDF media are legitimate learner assets and must keep publishing normally (a blanket `.pdf` entry in `SKIP_EXTENSIONS` would break them).
- **FR-7.4:** It SHALL be documented that under `OUTPUT_BACKEND=oci` the preserved PDF is uploaded to the public-read media bucket at conversion time and is therefore fetchable by anyone holding its URL. This is acknowledged as roughly equivalent to the page rasters already published there, which reproduce the same content. Hardening via pre-authenticated, expiring URLs is noted as a follow-up, not part of this fix.

### FR-8: Backward compatibility

- **FR-8.1:** Documents that already have `-page-N.png` files (such as `00e3441e_class 9 ch1`) SHALL continue to work without repair.
- **FR-8.2:** The split-view script SHALL degrade to the current filename-guessing behavior when no descriptor is present, so pre-repair and post-fix HTML both work.
- **FR-8.3:** The fix SHALL work under both `OUTPUT_BACKEND=local` and `OUTPUT_BACKEND=oci`.
- **FR-8.4:** No change to the Docling extraction path's page-image behavior.
- **FR-8.5:** No change to the split-view CSS, the `#railSplitBtn` button, the enable/disable toggle logic, or the `MutationObserver` that exits split view when edit mode ends.

## Non-Functional Requirements

### NFR-1: Performance and storage

- **NFR-1.1:** Rasterisation SHALL use a DPI low enough to keep a 22-page document's added conversion time to a few seconds while staying legible for proofreading. Target ~100–150 DPI.
- **NFR-1.2:** Page rasters SHALL remain lazily loaded so opening split view on a long document does not fetch every page at once.
- **NFR-1.3:** Preserving the PDF adds roughly one source-document-worth of storage per job. Combined with rasters this SHOULD stay proportionate to the existing image payload, and MUST be measured during verification rather than assumed.

### NFR-2: Safety

- **NFR-2.1:** The repair tool operates across all existing jobs and so SHALL default to the safest behavior: `--dry-run` output must be inspected before a real run, published content is never rewritten, and the original HTML SHALL be backed up before patching (the `.html.bak` convention already present in `output/00e3441e_class 9 ch1/`).
- **NFR-2.2:** No change to retention semantics or to invariant I8.

## Out of Scope

- Scroll or page synchronization between the two panes (deliberately absent per `document.html:8848-8849`).
- Bundling a JavaScript PDF renderer. The native viewer satisfies FR-2.2 with no new dependency; revisit only if verification shows it unusable.
- Retroactively re-publishing learner pages. Split view is editor-only and stripped on publish, so learner output is unaffected by this fix.
- Pre-authenticated/expiring URLs for the preserved PDF (FR-7.4 follow-up).

## Acceptance Criteria

1. A newly converted PDF (`OCR_ENGINE=surya`) yields, in its output directory, a preserved source PDF plus `<stem>-page-1.png … -page-N.png` where N equals the PDF's page count.
2. Opening that document in edit mode and clicking split view shows the **real PDF** in the right pane, with selectable text, working search, and zoom.
3. Deleting or corrupting the preserved PDF for that document makes the pane fall back to the page rasters, still labelled `Page 1 … Page N`.
4. Removing both the PDF and the rasters makes the pane show "Original page images are not available for this document." — never a blank pane.
5. Removing only `-page-3.png` (rasters tier active) leaves the other pages visible, hides page 3, and shows no message.
6. Under `OUTPUT_BACKEND=oci`, the emitted descriptor holds absolute media-bucket URLs for both the PDF and the rasters, and both load in the browser.
7. `00e3441e_class 9 ch1` still renders in split view with its existing baked HTML untouched.
8. Running the repair tool with `--dry-run` across all jobs reports intended actions and changes nothing on disk.
9. Running the repair tool against a job whose upload PDF still exists produces a working PDF-backed split view for that document.
10. Running it against `02902739_ihga104`, whose source PDF is gone, patches the HTML so the pane shows the FR-5 message, and reports the missing source explicitly.
11. The repair tool leaves the bucket HTML of published jobs untouched, verified by checking that a published learner page still has no editor chrome after a full run.
12. Re-running the repair tool over an already-repaired corpus reports all jobs skipped and makes no writes.
13. Publishing a post-fix document produces learner HTML with no `originalSplitAssets` descriptor, no `railSplitBtn`, no `split-source`, and no reference to the preserved PDF; an editor-attached PDF in the same document still publishes to the media bucket.
14. Editing the document header text does not break the pane.
