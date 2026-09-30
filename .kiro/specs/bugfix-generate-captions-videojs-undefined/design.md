# Bug Fix Design: "Generate Captions" Fails with "videojs is not defined"

## Design Goal

Stop the caption button from throwing "videojs is not defined", and make the button honest about where it can work: functional in the editor (where the `/api/generate-captions` route and Video.js exist), and absent/non-actionable on the static published page (where they do not).

## As-is behavior (confirmed)

- `generateCaptions()` (`document.html:8052-8080`) calls `videojs.getPlayer(videoId)` **bare and unguarded** at `document.html:8067`. Inside a promise `.then()`, so its `ReferenceError` is caught by the function's `.catch` and printed as `Error: videojs is not defined` in the `#<id>-caption-status` span.
- Every other Video.js call guards with `window.videojs` (`document.html:5454,5499,5517,7632`; `s3_publish.py:546,626,1447`).
- The server route `POST /api/generate-captions/<path>` (`app.py:2077-2107`, faster-whisper) exists only in the running Flask app, never on the static OCI host.
- `_strip_editor_ui` (`s3_publish.py:190-236`) removes the editor script that defines `generateCaptions` (region from `document.html:3574` `<!-- Formula Input Dialog -->` to `</body>`) but keeps the Video.js head tags. The published learner runtime has no caption UI and guards `videojs`.

## Fix strategy overview

| # | Change | File | Satisfies |
|---|---|---|---|
| 1 | **Guard the bare `videojs.getPlayer`** and degrade gracefully | `python_app/templates/document.html` | FR-1, RC-1 |
| 2 | **Clearer editor-side status** on failure (no raw exception text) | `python_app/templates/document.html` | FR-3.2, NFR-1/2 |
| 3 | **Neutralise caption UI in the published view** (strip any baked-in Generate Captions button/status) | `python_app/s3_publish.py` | FR-2, RC-2, RC-3 |
| 4 | **(Confirm) publish the saved VTT track** so learners get captions without generating | `python_app/s3_publish.py` (verify) | FR-3.3 |

Change 1 is the minimal, always-correct fix for the literal error. Change 3 addresses why a failing button is on the published page at all. Changes 2 and 4 are correctness/robustness.

---

## Change 1 — Guard the bare `videojs.getPlayer` (the literal fix)

At `document.html:8067`, replace:

```js
var player = videojs.getPlayer(videoId);
```

with a guarded lookup consistent with the rest of the codebase:

```js
var player = (window.videojs && typeof window.videojs.getPlayer === 'function')
  ? window.videojs.getPlayer(videoId) : null;
```

`if (player) { player.addRemoteTextTrack(...) }` already follows, so a `null` player is handled — the track simply is not attached, and the status can say captions were generated but could not be attached to this player. This removes the `ReferenceError` unconditionally (FR-1.2, FR-1.3).

**Why this alone is not the whole fix:** it stops the raw error, but on the published page the `POST /api/generate-captions/...` still cannot succeed (no server), so the button would just fail differently. Change 3 makes the button not appear there.

---

## Change 2 — Honest editor-side status messages

In `generateCaptions`, keep developer detail in the console but show plain-language status to the user (FR-3.2, NFR-1, NFR-2):

- On network/route failure (e.g. 404/405, or fetch rejection): status → something like "Caption generation isn't available here." while `console.error` logs the real error.
- On server `success:false`: show `data.error` if it is user-safe, else a generic "Couldn't generate captions. Please try again."
- On success but no attachable player: "Captions generated." (the VTT is saved server-side regardless; attaching to the live player is best-effort).

This keeps the raw `err.message` (which produced "videojs is not defined") out of the UI even for other unexpected errors.

---

## Change 3 — Neutralise the caption UI in the published view

The caption button is meaningful only in the editor. On publish, ensure no actionable Generate Captions control remains (FR-2.2, FR-2.3, RC-3).

**Preferred approach — strip baked-in caption controls in `_strip_editor_ui`.** The runtime-built popups do not persist, but caption button/status markup can end up in the saved body. Add a targeted removal in `_strip_editor_ui` (`s3_publish.py`) that deletes:
- any element whose `onclick` calls `generateCaptions(`, and
- any `#…-caption-status` span,

so a learner never sees a button wired to a stripped function or an unreachable route. This mirrors the module's existing pattern of removing editor-only affordances (e.g. `contenteditable`, `draggable`, block-controls).

**Rejected alternatives:**
- *Leave the button but disable it on published pages* — still ships a dead control and depends on runtime detection; stripping is cleaner and matches how other editor UI is handled.
- *Only guard `videojs` (Change 1) and stop* — leaves a button that fails its `fetch` on the published page (route 404), i.e. still broken, just with a different message.

**Editor retains the button** because the popup markup is built at runtime by `showMediaContent` in the editor script (which is present in the editor and stripped on publish), so the editor keeps a working button while the published page carries none.

---

## Change 4 — Verify the saved VTT track is published (FR-3.3)

Caption generation writes `<video>.vtt` alongside the video (`app.py:2096`). For learners to get captions without generating them:
- Confirm the editor attaches/persists a `<track kind="captions" src="...vtt">` (or equivalent) when captions are generated and the document is saved.
- Confirm `s3_publish` uploads `.vtt` files (they are media assets) and rewrites their `src` to the bucket URL like other media.

If the editor does not currently persist a `<track>` element, note it as a follow-up; the core reported bug (the raw error / broken button) is fixed by Changes 1 and 3 regardless. This change is verification-first; only extend if a gap is found.

---

## Files Changed (anticipated)

| File | Change |
|---|---|
| `python_app/templates/document.html` | Guard `videojs.getPlayer` at line ~8067; plain-language caption status messages |
| `python_app/s3_publish.py` | In `_strip_editor_ui`, remove baked-in Generate Captions buttons and `-caption-status` spans; (verify) `.vtt` upload + `<track>` publishing |

**Deliberately unchanged:** the `/api/generate-captions` route and faster-whisper pipeline (they work in the editor context), other media popup logic, and the existing `window.videojs` guards elsewhere.

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Strip regex over-matches and removes non-caption markup | Medium | Target precisely: elements with `onclick*="generateCaptions("` and `id$="-caption-status"`; verify against a real saved doc |
| Guard change hides a genuine editor bug | Low | Console still logs the real error (NFR-2) |
| VTT track not persisted → learners still lack captions | Low-Medium | Change 4 verifies; treat missing persistence as a scoped follow-up, not a blocker for the error fix |
| Editor button accidentally stripped too | Low | Editor popup is built at runtime by the editor script; publish stripping only touches baked/published HTML |

## Verification Plan

1. **Editor, Video.js present:** Generate Captions attaches a VTT track on success; no "videojs is not defined" (AC-1).
2. **Video.js absent (simulate):** the guarded path returns `null`, no throw, status is a plain message (AC-2).
3. **Published page:** confirm no actionable Generate Captions button remains; if a baked-in one existed, it is stripped (AC-3, AC-4).
4. **Editor failure messaging:** force a 404/500 from the route; UI shows plain text, console has detail (AC-5).
5. **Non-regression:** video playback and audio/pptx/h5p/vlab/url/glossary popups unaffected in editor and published views (AC-6).
6. **VTT publishing:** a document with generated+saved captions carries the caption track on the published page, or the gap is documented as a follow-up (AC-7).
7. **Syntax/build:** the edited `document.html` script blocks and `s3_publish.py` remain valid (py_compile for the module; balanced-delimiter check for injected/edited JS).
