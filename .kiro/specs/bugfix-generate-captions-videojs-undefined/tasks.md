# Bug Fix Tasks: "Generate Captions" Fails with "videojs is not defined"

Ordered so the literal error is fixed first (Task 1), then the reason the broken button appears on the published page (Task 2), then correctness/verification (Tasks 3–4), then end-to-end verification (Task 5).

---

## Task 1: Guard the bare `videojs.getPlayer` call (the literal fix)

**Files:** `python_app/templates/document.html`
**Satisfies:** FR-1, RC-1

- [ ] 1.1: In `generateCaptions` (`document.html:8052-8080`), locate line ~8067: `var player = videojs.getPlayer(videoId);`.
- [ ] 1.2: Replace with a guarded lookup matching the codebase convention:
  ```js
  var player = (window.videojs && typeof window.videojs.getPlayer === 'function')
    ? window.videojs.getPlayer(videoId) : null;
  ```
- [ ] 1.3: Confirm the existing `if (player) { player.addRemoteTextTrack(...) }` still follows, so a `null` player is handled without throwing.
- [ ] 1.4: When `player` is null but captions were generated, set the status to a plain message (e.g. "Captions generated.") rather than leaving it on "Captions ready!" with no track attached.

**Verify:** with Video.js absent on the page, the success branch no longer throws "videojs is not defined"; with it present, the track still attaches.

---

## Task 2: Neutralise caption UI in the published view

**Files:** `python_app/s3_publish.py`
**Satisfies:** FR-2, RC-2, RC-3

- [ ] 2.1: In `_strip_editor_ui` (`s3_publish.py:190-236`), add a targeted removal (beside the existing editor-affordance strips like `contenteditable`/`draggable`/`block-controls`) that deletes:
  - any element whose `onclick` attribute contains `generateCaptions(`, and
  - any element with an id ending in `-caption-status`.
- [ ] 2.2: Make the patterns precise so they cannot match unrelated markup (anchor on `onclick="...generateCaptions(` and `id="..."-caption-status"`); do not remove the surrounding video element.
- [ ] 2.3: Add a comment explaining why: the caption feature depends on the `/api/generate-captions` route and Video.js integration that exist only in the running editor, so any baked-in button must not reach learners as a live-but-broken control.
- [ ] 2.4: Confirm the editor keeps a working button — the popup markup is built at runtime by `showMediaContent` in the editor script, which is only present in the editor and stripped on publish, so this change affects only published HTML.

**Verify:** publish a document whose saved body contains a Generate Captions button; the published HTML contains no `generateCaptions(` onclick and no `-caption-status` span, and the video still plays.

---

## Task 3: Honest editor-side caption status messages

**Files:** `python_app/templates/document.html`
**Satisfies:** FR-3.2, NFR-1, NFR-2

- [ ] 3.1: In `generateCaptions`, change the `.catch` and failure branches to show plain-language status text (e.g. "Couldn't generate captions. Please try again." or "Caption generation isn't available here.") instead of `'Error: ' + err.message`.
- [ ] 3.2: Keep the real error in `console.error(...)` for developers (NFR-2).
- [ ] 3.3: For `success:false`, show `data.error` only if it is user-safe; otherwise a generic message.

**Verify:** force a route 404/500 and a rejected fetch; the status span shows plain text while the console carries the detail.

---

## Task 4: Verify saved captions publish as a track (FR-3.3)

**Files:** `python_app/s3_publish.py` (verify; extend only if a gap is found), `python_app/templates/document.html` (verify persistence)

- [ ] 4.1: Confirm that when captions are generated and the document is saved, a `<track kind="captions" src="...vtt">` (or equivalent) is persisted on the video element.
- [ ] 4.2: Confirm `s3_publish` uploads `.vtt` files as media and rewrites their `src` to the bucket URL like other media assets.
- [ ] 4.3: If persistence is missing, record it as a scoped follow-up; it does not block the error fix (Tasks 1–2). Do not add speculative persistence code without confirming the current behavior first.

**Verify:** a document with generated+saved captions carries the caption track on the published page, or the gap is documented.

---

## Task 5: End-to-end verification

- [ ] 5.1: **Editor, Video.js present** — Generate Captions attaches a VTT track on success; no "videojs is not defined" (AC-1).
- [ ] 5.2: **Video.js absent** — the guarded path returns null, no throw, plain status (AC-2).
- [ ] 5.3: **Published page** — no actionable Generate Captions button; a baked-in one is stripped (AC-3, AC-4).
- [ ] 5.4: **Editor failure messaging** — route 404/500 shows plain text; console has detail (AC-5).
- [ ] 5.5: **Non-regression** — video playback and audio/pptx/h5p/vlab/url/glossary popups unaffected in editor and published views (AC-6).
- [ ] 5.6: **VTT publishing** — captions track present on published video, or gap documented (AC-7).
- [ ] 5.7: **Build/syntax** — `python3 -m py_compile python_app/s3_publish.py`; balanced-delimiter check on edited `document.html` script region; confirm existing `window.videojs` guards elsewhere are intact.

---

## Suggested Sequencing

Task 1 is the minimal fix for the reported error and can ship immediately. Task 2 removes the broken button from published pages and should ship with it. Tasks 3–4 are correctness/robustness. Task 5 verifies everything.

## Definition of Done

- Tasks 1–3 implemented; Task 4 verified (or gap documented); Task 5 passes.
- No user-facing "videojs is not defined" from the caption button in either context.
- Published learner pages carry no dead/broken Generate Captions control.
- Editor caption generation still works and reports failures in plain language.
- No regression to video playback or other media popups.


---

## Task 4 Verification Result (VTT persistence)

Confirmed during implementation:

- Generated captions are attached to the player **only at runtime** via `player.addRemoteTextTrack(...)` (`document.html`, in `generateCaptions`). No `<track kind="captions">` element is written into the saved document DOM.
- `.vtt` is **not** in `SKIP_EXTENSIONS` (`s3_publish.py:42`), so a `.vtt` file saved beside the video **would** upload to the media bucket during publish.

**Gap (documented, not fixed here):** because no `<track>` is persisted in the saved HTML, a published learner page does not automatically show captions even when the `.vtt` was generated and uploaded. Making learners get captions without generating requires persisting a `<track kind="captions" src="…vtt">` on save and rewriting its `src` at publish time. This is a **separate enhancement**, out of scope for the reported "videojs is not defined" bug, which is fully addressed by Tasks 1–3. Recommend tracking it as a follow-up (FR-3.3 / AC-7).
