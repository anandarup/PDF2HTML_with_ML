# Bug Fix Design: H5P Activity Opens as a Link Instead of Embedding in the Popup

## Design Goal

When a learner clicks an H5P activity on a published chapter, the interactive activity SHALL load and run **inside** the popup. The bare "Open activity" link SHALL appear only as a genuine, explained last resort — and the reason for any failure SHALL be diagnosable.

## Diagnosis First — pin the actual cause before coding

The remedies for the candidate causes differ, so the fix begins with a **confirmation step** against the reported document, not a code change. This is the highest-leverage part of the plan and is deliberately Task 1.

Confirm, for the reported published page `97d9c0ff_lemh105` (and one freshly published test document):

1. **Runtime present?** In the browser console on the published page, check `typeof window.H5PStandalone`. If `undefined`, the unpkg CDN bundle is not loading → **RC-3a** dominates.
2. **Package reachable?** Read the chip's `data-media-src`, then fetch `<src>/h5p.json` directly. 404/redirect-to-HTML → **RC-3b** (package not uploaded to the media bucket, or the media-chip `src` was never rewritten to the bucket URL).
3. **Asset resolution?** If `h5p.json` loads but the console shows library/asset 404s or `library:"undefined"`, → **RC-3c/RC-3d** (absolutization or package graph).
4. **Which handler ran?** Confirm whether the learner-runtime per-button handler or the reader-shell delegated handler produced the body (the title "H5p" vs "Activity" tells us, and the body link text "Open activity" vs "Open activity files" tells us which builder ran). This confirms **RC-2**.

The design below fixes **RC-1/RC-2 unconditionally** (they are defects regardless), and applies the **RC-3/RC-4** remedies gated on what step 1–3 confirm.

## Current architecture (as-is)

Published output is baked HTML uploaded to OCI. `python_app/s3_publish.py` injects two client scripts before `</body>`:

| Script | Popup title for h5p | H5P body builder | Link fallback text |
|---|---|---|---|
| Learner-runtime | `type[0].toUpperCase()+slice(1)` → **"H5p"** | `h5pMount` + `h5pWatch` (full) | `h5pFail` → **"Open activity files"** |
| Reader-shell (delegated) | `MI.h5p` → **"Activity"** | `build('h5p')` (reduced) | terminal → **"Open activity"** |

The reader-shell handler is a document-level click listener guarded by "if popup already visible, return." The intent was: learner-runtime binds per-button and calls `stopPropagation()`; the delegated one is a safety net. In practice the reported symptom is a **cross** (title "H5p" from learner-runtime, link "Open activity" from reader-shell `build()`), so the two are interacting and the **reduced** `build('h5p')` — which falls back to a link — is what renders. That is the core client defect.

Server side (unchanged intent): `/upload_media` → `_extract_h5p` → `normalize_h5p_package` (validates the library graph, canonicalises versioned dirs) → returns `media/<folder>`; publish uploads bundle roots whole via `_find_h5p_roots` (`BUNDLE_MARKERS = h5p.json, index.html`). There is **no** server-side H5P runtime; embedding is 100% client-side.

## Fix strategy overview

| # | Change | File(s) | Satisfies | Gated on |
|---|---|---|---|---|
| 1 | **Diagnose** the reported document; record the confirmed cause | — (investigation) | A-1, A-2, AC-8 | always |
| 2 | **Single H5P popup path** using the full mount logic | `python_app/s3_publish.py` | FR-2, RC-1, RC-2 | always |
| 3 | **Absolutise `src`** consistently for chip + inline in the published scripts | `python_app/s3_publish.py` | FR-3.2, RC-3c | always (cheap, correct) |
| 4 | **Resilient player runtime** (integrity + fallback, or self-host the bundle) | `python_app/s3_publish.py`, `python_app/templates/document.html` | FR-4, NFR-1 | if RC-3a confirmed |
| 5 | **Verify/repair package hosting + URL** on OCI | `python_app/s3_publish.py` (verify), possibly `app.py` | FR-3.1, FR-3.3, RC-3b | if RC-3b confirmed |
| 6 | **Explained, diagnosable failure** replacing the bare link | `python_app/s3_publish.py` | FR-5 | always |
| 7 | **Repair tool** for existing published pages (only if needed) | `python_app/tools/repair_h5p_popup.py` (new) | FR-6.2, AC-9 | if existing pages must be fixed |

The load-bearing decisions are **Change 2** (stop the reduced builder from winning) and **Change 4** (make the runtime actually available). Changes 3 and 6 are correctness/robustness that apply regardless.

---

## Change 2 — One H5P popup path, using the full mount logic

**Problem:** two handlers populate `#mediaPopup`; the reduced `build('h5p')` can render the body and it falls back to a link before a real embed is attempted well. Titles diverge ("H5p" vs "Activity").

**Design:**
- Make the reader-shell delegated handler **reuse the same full H5P mount** used by the learner-runtime (`h5pMount` + `h5pWatch`), rather than its own reduced `build('h5p')` branch. Extract the H5P mount into a single shared function referenced by both handlers, or have the delegated handler defer to the learner-runtime mount, so there is exactly one embedding implementation (FR-2.1, FR-2.2).
- Guarantee **exactly one** handler renders per click. Options, decided during implementation against the confirmed behavior:
  - keep the learner-runtime per-button binding authoritative and make the delegated listener a true no-op when a per-button handler exists; or
  - remove the per-button binding and make the single delegated handler the one authoritative path.
  The chosen option MUST eliminate the cross where one handler titles and the other builds.
- **Unify the title** to one agreed label for H5P (e.g. "Activity"), independent of which handler runs (FR-2.3).

**Why not just delete one script:** each has non-H5P responsibilities (labels, other media types, drawer/notes wiring). The change is surgical: unify the H5P branch and the title, and make the render single-owner — not wholesale removal.

**Rejected alternative:** leave both handlers and only fix the link text. Rejected — it does not make the activity embed; it just relabels the failure.

---

## Change 3 — Absolutise `src` consistently (published scripts)

Packages declare `embedTypes:["iframe"]`; inside that iframe, relative asset URLs resolve against the iframe, not the page, so `h5p.json` can load while content assets 404. The learner-runtime `h5pMount` already uses `h5pAbs(src)`; the reduced `build('h5p')` did its own `new URL(src, document.baseURI)`. Once Change 2 makes both use one mount, `src` absolutization happens in one place, for both chip (`data-media-src`) and inline (`data-h5p-src`). This satisfies FR-3.2 and removes RC-3c as a variable.

---

## Change 4 — Resilient player runtime (apply iff RC-3a confirmed)

**If diagnosis shows `window.H5PStandalone` is undefined on the published page**, the CDN is the cause. Two acceptable remedies; design prefers **4B** for the DIKSHA/OCI target because it removes the external dependency at view time (NFR-1), but 4A is lower-effort if the environment does reach unpkg:

- **4A — Harden the CDN reference.** Keep unpkg but add `crossorigin`/`integrity` (SRI) and a scripted fallback that, on load error, injects an alternate host (e.g. jsDelivr) before declaring failure. Keeps zero build/hosting changes.
- **4B — Self-host the player bundle with the output.** Vendor `main.bundle.js`, `frame.bundle.js`, and `styles/h5p.css` into the app's static assets and upload them alongside published output (or reference them from a first-party static path), so the published page loads the runtime from the same origin/bucket as the content. This is the most robust against blocked third-party CDNs. Requires:
  - placing the three files where publish already uploads assets (mirroring how `_find_h5p_roots` carries bundle directories intact),
  - pointing `frameJs`/`frameCss`/main script at the first-party URL in **all** mount sites (`s3_publish.py` runtime + `document.html` editor + inline rebuild),
  - ensuring these references are **not** stripped by `_strip_editor_ui` (FR-4.2).

**Decision rule:** choose 4A if diagnosis shows unpkg is reachable but flaky; choose 4B if it is blocked in the target environment. Record the decision in the task notes with the diagnostic evidence.

---

## Change 5 — Verify/repair package hosting + URL on OCI (apply iff RC-3b confirmed)

**If `<src>/h5p.json` 404s or redirects to HTML on the published page**, the package directory did not reach the media bucket, or the chip `src` was not rewritten to the bucket URL. Design:

- Confirm `_find_h5p_roots` detects the package for this document (marker `h5p.json`) and that `s3_publish` uploads the whole directory (it must not apply `SKIP_EXTENSIONS` inside a bundle root — verify this still holds).
- Confirm the media-chip `data-media-src` and inline `data-h5p-src` literal `media/<folder>` strings are rewritten to absolute bucket URLs during publish, the same way image `src`s are. If chips are missed by the rewrite (because their value is an attribute the rewrite pass does not visit), extend the rewrite to cover them.
- Confirm the output store serves the package directory objects with sensible content types (JS/CSS/JSON) so the player can execute them.

No change to the extract/normalize/validate flow unless a defect is found there (FR-6.4).

---

## Change 6 — Explained, diagnosable failure (always)

Replace the bare "Open activity" / "Open activity files" endpoints with a single shared failure renderer that:
- shows a **non-technical message** ("This interactive activity could not be loaded.") plus, optionally, a secondary link (FR-5.1, FR-5.3);
- **categorises the cause** — runtime-missing vs package-unreachable vs libraries-unresolved — and logs it distinctly to the console with a stable prefix (`[PDF2HTML:H5P]`), so the mode is diagnosable (FR-5.2, NFR-2);
- never leaks bucket names/paths/stack traces to the learner (FR-5.4).

This unifies the two divergent fallbacks (`h5pFail` "Open activity files" and `build()` "Open activity") into one, consistent with Change 2's single path.

---

## Change 7 — Repair existing published pages (only if required)

Published pages are baked HTML served verbatim; they are **not** re-rendered from the template. If already-published documents must embed correctly without re-publishing, a repair tool is needed, mirroring the split-view bugfix's approach:

- `python_app/tools/repair_h5p_popup.py`, with `--job`, `--all`, `--dry-run`.
- Patch the injected H5P popup script region in each baked HTML to the corrected version; back up to `<name>.html.bak` first; idempotence guard on a marker; per-job reporting.
- **Publish-safety gate:** re-upload to the HTML bucket only for jobs that are **not** published if that would overwrite learner content with a mismatched variant — reuse the published-key hazard analysis and `_is_published()` check from the split-view spec. For published jobs, patch the local copy and report `patched-local-only`, or (preferred) re-publish through the normal pipeline so the corrected runtime is emitted.
- Preferred simpler path if acceptable to the requester: **re-publish** affected chapters through the normal pipeline once Changes 2–6 land, avoiding a bespoke HTML-patching tool. Decide with the requester.

---

## Files Changed (anticipated)

| File | Change |
|---|---|
| `python_app/s3_publish.py` | Single H5P popup path shared by both injected scripts; unified title; one absolutised mount; one diagnosable failure renderer; (Change 5) verify/extend chip/inline `src` bucket rewrite; (Change 4B) first-party runtime references |
| `python_app/templates/document.html` | (Change 4B only) point editor + inline-rebuild mount sites at the first-party runtime; keep behavior otherwise identical |
| `python_app/app.py` | Only if Change 5 finds a hosting/serving defect (content type or upload gap) |
| `python_app/tools/repair_h5p_popup.py` | **New, only if** existing baked pages must be fixed without re-publishing |

**Deliberately unchanged unless diagnosis requires:** `_extract_h5p`, `normalize_h5p_package`, `_find_h5p_roots` core logic, and all non-H5P media popup branches.

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Fixing the wrong cause (e.g. hardening CDN when the real issue is package hosting) | **High** | Task 1 diagnosis gates Changes 4/5; do not code a remedy before its cause is confirmed on the reported doc |
| Repair tool overwrites published learner HTML | **High** | Prefer re-publish over patching; if patching, reuse split-view `_is_published()` gate, `.bak` backup, `--dry-run` |
| Unifying handlers breaks another media type | Medium | Change is scoped to the H5P branch + title; regression-test video/audio/pptx/vlab/url/glossary (AC-7) |
| Self-hosted bundle version drift from CDN | Medium | Pin to the same `3.8.0` currently referenced; document the vendored version |
| Absolutization breaks a legitimate `.html` page embed | Low-Medium | Preserve the `h5pIsPage` branch; only packages are absolutised for the player |
| CSP on the published host blocks inline/eval used by the player | Medium | Check during diagnosis; self-hosting (4B) plus documented CSP allowances if needed |

## Verification Plan

1. **Diagnosis recorded:** the confirmed cause for `97d9c0ff_lemh105` is written down with the console/network evidence (AC-8).
2. **Fresh publish, media chip:** publish a chapter with a valid H5P chip; click → activity embeds and is interactive; no "Open activity" link (AC-1).
3. **Single handler + title:** confirm exactly one handler renders and the title is the single agreed label (AC-2).
4. **Inline Insert-menu H5P:** renders embedded in the published view (AC-3).
5. **Pre-built `.html` page:** still embeds via iframe (AC-4).
6. **OCI reachability:** `<src>/h5p.json` fetches 200 with correct content type; package assets resolve; activity embeds (AC-5).
7. **Runtime unavailable path:** simulate `window.H5PStandalone` undefined → popup shows the explained message and a categorised console diagnostic, not a bare link (AC-6).
8. **Non-regression:** video, audio, pptx, vlab, url, glossary popups unchanged (AC-7).
9. **Reported document:** the specific chapter (re-published or repaired) embeds its H5P activity in the popup (AC-8).
10. **Repair/re-publish path (if used):** dry-run changes nothing; published learner pages are not overwritten with mismatched content; backups exist (AC-9).
11. **Editor preview:** pre-publish H5P preview still works (FR-6.3).
