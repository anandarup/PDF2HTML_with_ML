# Bug Fix Tasks: H5P Activity Opens as a Link Instead of Embedding in the Popup

Ordered so the cause is confirmed before any remedy is coded. Task 1 is investigation and gates the conditional tasks. Tasks 2, 3, and 6 are unconditional client fixes in the published scripts. Tasks 4 and 5 are conditional on what Task 1 confirms. Task 7 (repair/re-publish existing pages) is last and only if required. Task 8 is end-to-end verification.

---

## Task 1: Diagnose the actual cause on the reported document

**Files:** none (investigation); record findings in this spec.
**Satisfies:** A-1, A-2, AC-8 (foundation)

- [ ] 1.1: Open the reported published page (`97d9c0ff_lemh105`) and confirm the H5P was added via the **per-section media chip** (`.media-icon[data-media-type="h5p"]`), matching the "Open activity" symptom (A-1). Note the chip's `data-media-src`.
- [ ] 1.2: In the browser console on that page, check `typeof window.H5PStandalone`. Record present/undefined → confirms or rules out **RC-3a** (CDN/runtime).
- [ ] 1.3: Fetch `<data-media-src>/h5p.json` directly in the browser/devtools. Record status and content type → confirms or rules out **RC-3b** (package not hosted / wrong URL / served as HTML).
- [ ] 1.4: If `h5p.json` loads, watch the network/console for library or content-asset 404s and `library:"undefined"` → confirms or rules out **RC-3c/RC-3d** (absolutization / package graph).
- [ ] 1.5: Determine which popup handler rendered: title **"H5p"** (learner-runtime) vs **"Activity"** (reader-shell), and body link **"Open activity"** (reader-shell `build`) vs **"Open activity files"** (`h5pFail`). Confirms **RC-2** (handler cross).
- [ ] 1.6: Repeat 1.2–1.5 against a **freshly published** test chapter with a known-valid H5P package, to separate "old baked page" issues from "current pipeline" issues.
- [ ] 1.7: Write the confirmed cause(s) into this spec (a short "Diagnosis Result" note), and mark which conditional tasks (4 and/or 5) apply. Do not code a remedy whose cause was not confirmed.

**Verify:** the spec contains an explicit, evidence-backed statement of the cause; Tasks 4/5 are marked applicable or not.

---

## Task 2: One H5P popup path using the full mount logic

**Files:** `python_app/s3_publish.py`
**Satisfies:** FR-2.1, FR-2.2, FR-2.3, RC-1, RC-2

- [ ] 2.1: Locate the two H5P popup code paths in the injected scripts: the learner-runtime per-button handler (title `type[0].toUpperCase()+slice(1)` → "H5p"; body via `h5pMount`) and the reader-shell delegated handler (title `MI.h5p` → "Activity"; body via `build('h5p')`).
- [ ] 2.2: Make both handlers use **one** H5P embed implementation — the full `h5pMount` + `h5pWatch` (which genuinely attempts the player and only then fails). Remove the reduced `build('h5p')` embedding branch that falls back to "Open activity" before a real attempt, or replace its body with a call to the shared mount.
- [ ] 2.3: Ensure **exactly one** handler renders per click. Either keep the per-button binding authoritative and make the delegated listener a strict no-op when a per-button handler is present, or consolidate to a single delegated handler. Eliminate the title/build cross.
- [ ] 2.4: **Unify the popup title** for H5P to one agreed label (default: "Activity") regardless of which handler runs (FR-2.3). Apply the same treatment only to h5p; leave other media titles as-is.
- [ ] 2.5: Do not alter non-H5P branches of either handler (FR-6.1).

**Verify:** in a rendered test page, an H5P chip click produces one popup, one title, and reaches the full mount code (add a temporary console marker if needed) rather than the link branch.

---

## Task 3: Absolutise `src` consistently for chip and inline

**Files:** `python_app/s3_publish.py`
**Satisfies:** FR-3.2, RC-3c

- [ ] 3.1: Ensure the single shared mount from Task 2 absolutises the package `src` (`new URL(src, document.baseURI).href`, i.e. the existing `h5pAbs`) for **both** the media-chip (`data-media-src`) and inline (`data-h5p-src`) paths, so iframe-relative asset URLs resolve.
- [ ] 3.2: Preserve the `h5pIsPage` branch: a pre-built `.html` page is iframed directly and must not be treated as a package (FR-1.4).
- [ ] 3.3: Confirm the inline rebuild loop (`.h5p-inline-container[data-h5p-src]`) uses the same absolutised mount (FR-1.3).

**Verify:** with a package whose content references relative images, the embedded activity shows its images (not broken) on the published page.

---

## Task 4: Resilient player runtime — CONDITIONAL on RC-3a

**Files:** `python_app/s3_publish.py`, `python_app/templates/document.html`
**Satisfies:** FR-4, NFR-1
**Apply only if Task 1 confirmed `window.H5PStandalone` was undefined.**

Choose 4A or 4B per Task 1 evidence and record the decision:

- [ ] 4A (CDN reachable but flaky): add `crossorigin` + Subresource Integrity to the `h5p-standalone@3.8.0` script references, and a scripted onerror fallback to an alternate host (e.g. jsDelivr) before failure is declared. Update the main bundle reference and the `frameJs`/`frameCss` used at every mount site.
- [ ] 4B (CDN blocked in target): vendor `main.bundle.js`, `frame.bundle.js`, and `styles/h5p.css` (pinned to `3.8.0`) into first-party static assets; upload them with published output; point the main script and every `frameJs`/`frameCss` (in `s3_publish.py` runtime, `document.html` editor, and the inline rebuild) at the first-party URLs.
- [ ] 4.3: Ensure the runtime reference survives publishing — confirm `_strip_editor_ui` does not remove it (FR-4.2).
- [ ] 4.4: Keep all mount sites pointing at the **same** version to avoid drift.

**Verify:** on a page loaded in the target-like environment (or with unpkg blocked), `window.H5PStandalone` is defined and a valid activity embeds.

---

## Task 5: Verify/repair OCI package hosting and `src` rewrite — CONDITIONAL on RC-3b

**Files:** `python_app/s3_publish.py` (verify; extend rewrite if needed), `python_app/app.py` (only if a serving/content-type defect is found)
**Satisfies:** FR-3.1, FR-3.3, RC-3b
**Apply only if Task 1 confirmed `<src>/h5p.json` was unreachable / mis-served.**

- [ ] 5.1: Confirm `_find_h5p_roots` detects the package (marker `h5p.json`) for the affected document and that the whole directory uploads intact (SKIP_EXTENSIONS not applied inside a bundle root).
- [ ] 5.2: Confirm the media-chip `data-media-src` and inline `data-h5p-src` literal `media/<folder>` values are rewritten to absolute bucket URLs during publish (as image `src`s are). If the rewrite pass does not visit these attributes, extend it to cover them.
- [ ] 5.3: Confirm the output store serves package objects with correct content types (`.js`, `.css`, `.json`) so the player can execute them; fix in `app.py`/output store only if a mismatch is found.
- [ ] 5.4: Do not change `_extract_h5p`/`normalize_h5p_package` unless a specific defect is found (FR-6.4).

**Verify:** after publish, `<src>/h5p.json` returns 200 with a JSON content type and package JS/CSS load; the activity embeds under `OUTPUT_BACKEND=oci`.

---

## Task 6: Explained, diagnosable failure state

**Files:** `python_app/s3_publish.py`
**Satisfies:** FR-5

- [ ] 6.1: Replace the two divergent fallbacks (`h5pFail` "Open activity files" and `build()` "Open activity") with a single shared failure renderer.
- [ ] 6.2: The renderer shows a non-technical message ("This interactive activity could not be loaded.") and MAY include a secondary link, but the link is never the primary experience when embedding is possible (FR-5.1, FR-5.3).
- [ ] 6.3: Categorise the cause — runtime-missing vs package-unreachable vs libraries-unresolved — and log it to the console with the stable `[PDF2HTML:H5P]` prefix (FR-5.2, NFR-2).
- [ ] 6.4: Ensure no bucket names, internal paths, or stack traces reach the learner-visible message (FR-5.4).

**Verify:** force each failure category (block the runtime; point at a missing package; corrupt libraries) and confirm the message plus a distinct, categorised console diagnostic in each case.

---

## Task 7: Fix existing published pages — CONDITIONAL and last

**Files:** `python_app/tools/repair_h5p_popup.py` (new) OR re-publish via existing pipeline
**Satisfies:** FR-6.2, AC-9
**Apply only if already-published chapters must embed without being re-published normally.**

- [ ] 7.1: Preferred: **re-publish** affected chapters through the normal pipeline once Tasks 2–6 land, so the corrected scripts/runtime are emitted. Confirm with the requester whether this is acceptable.
- [ ] 7.2: If bespoke patching is required instead: build `repair_h5p_popup.py` with `--job`, `--all`, `--dry-run`; back up each baked HTML to `<name>.html.bak`; idempotence guard on a marker; per-job reporting.
- [ ] 7.3: **Publish-safety gate:** reuse the split-view spec's `_is_published()` logic and the shared-object-key hazard analysis. Do not overwrite a published learner page with a mismatched variant; for published jobs prefer re-publish or report `patched-local-only`.
- [ ] 7.4: `--dry-run` performs no writes (no `.bak`, no upload).

**Verify:** covered by Task 8 steps for the repair/re-publish path.

---

## Task 8: End-to-end verification

- [ ] 8.1: **Diagnosis recorded** — the confirmed cause for `97d9c0ff_lemh105` is written in the spec with evidence (AC-8).
- [ ] 8.2: **Fresh media-chip H5P** — publish a chapter with a valid H5P chip; click → embeds and is interactive; no "Open activity" link (AC-1).
- [ ] 8.3: **Single handler + title** — exactly one handler renders; title is the single agreed label (AC-2).
- [ ] 8.4: **Inline Insert-menu H5P** — renders embedded in the published view (AC-3).
- [ ] 8.5: **Pre-built `.html` page** — still embeds via iframe (AC-4).
- [ ] 8.6: **OCI** — `<src>/h5p.json` 200 + correct type; package assets resolve; activity embeds (AC-5).
- [ ] 8.7: **Runtime-unavailable** — with the runtime blocked, popup shows the explained message and a categorised console diagnostic, not a bare link (AC-6).
- [ ] 8.8: **Non-regression** — video, audio, pptx, vlab, url, glossary popups unchanged (AC-7).
- [ ] 8.9: **Reported document** — the specific chapter (re-published or repaired) embeds its H5P activity in the popup (AC-8).
- [ ] 8.10: **Repair/re-publish** — if used, dry-run changes nothing; published pages are not overwritten with mismatched content; backups exist (AC-9).
- [ ] 8.11: **Editor preview** — pre-publish H5P preview still works (FR-6.3).
- [ ] 8.12: **Test suite** — run `python_app/tests/`, including any H5P/publish tests (e.g. `test_h5p_package`-style and `s3_publish` tests if present).

---

## Suggested Sequencing

Task 1 first, always. Tasks 2, 3, 6 are the unconditional client fixes and can ship together. Tasks 4 and 5 are added only if Task 1 confirms their cause. Task 7 follows once 2–6 are verified, and only if existing baked pages must be fixed without normal re-publish.

## Definition of Done

- Task 1 diagnosis is recorded; Tasks 2, 3, 6 implemented; Tasks 4/5 implemented iff their cause was confirmed; Task 8 verified.
- All applicable acceptance criteria in `requirements.md` pass.
- A freshly published chapter embeds its H5P activity in the popup; the bare "Open activity" link no longer appears when embedding is possible.
- No regression to non-H5P media popups or to the server-side extract/normalize/validate flow.
- No published learner page is overwritten with editor chrome or a mismatched variant.


---

## Diagnosis Result (Task 1)

Confirmed by reading the actual code and on-disk outputs (the reported job `97d9c0ff_lemh105` is not on this host, but sibling `*_lemh105` jobs and other H5P documents are, and the publish path is fully in `s3_publish.py`).

- **Runtime is present (RC-3a ruled out as primary).** `main.bundle.js` (defining `window.H5PStandalone`) is a `<script src>` in `document.html`'s `<head>` (line ~24), above the region `_strip_editor_ui` deletes (Formula-Dialog → `</body>`). It therefore survives publishing, so `window.H5PStandalone` is defined on the learner page in the normal case.
- **Package hosting/URL rewrite exists (RC-3b ruled out as primary).** `s3_publish.py` builds `h5p_map` prefix rewrites and applies them in a single pass, converting `media/<folder>` chip/inline `src`s to absolute media-bucket URLs. `_find_h5p_roots` uploads bundle directories whole.
- **Primary defect is RC-1 + RC-2 (client, `s3_publish.py`).** Two handlers populate `#mediaPopup`:
  - Runtime per-button handler: `stopPropagation()`, title `type[0].toUpperCase()+slice(1)` → **"H5p"**, body via the robust `h5pMount` + `h5pWatch` (fails via `h5pFail` → "Open activity files").
  - Reader-shell delegated document handler: title `MI.h5p` → **"Activity"**, body via a **near-duplicate reimplementation** `build('h5p')` whose terminal fallback is the bare **"Open activity"** link.
  The reported symptom (title "H5p" + "Open activity") is a **cross** between the two, and the duplicated reader-shell builder is the weaker one that ends in the bare link. Both reimplement the same H5P mount instead of sharing one.

**Conclusion:** Fix is unconditional Tasks **2, 3, 6** — make the reader-shell H5P branch reuse the runtime's single robust mount, guarantee one handler renders, unify the title, and route all failures through one explained/diagnosable renderer (no bare "Open activity" link as the primary output). Tasks **4** (runtime resilience) and **5** (OCI hosting) are **not required** for the primary defect; they remain optional hardening. Task **7** (repair existing baked pages) applies only if already-published chapters must be corrected without re-publishing — preferred remedy is re-publish after the fix lands.
