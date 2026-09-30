# Bug Fix Requirements: "Generate Captions" Fails with "videojs is not defined"

## Bug Summary

Clicking **Generate Captions** in a video popup shows the status text **"Error: videojs is not defined"** and no captions are produced.

**Reported context:** a video popup in what appears to be the **published learner view** (third-party player controls; page served from OCI object storage). The popup shows a playing video, a **Generate Captions** button, and the error text beside it.

## Expected vs Actual

| | Behavior |
|---|---|
| **Expected** | Either captions are generated and attached to the video, or — where caption generation is not available — the user is not presented with a button that fails. No raw "videojs is not defined" error is shown. |
| **Actual** | The status area shows "Error: videojs is not defined". No captions are added. |

## Root Cause

### RC-1: Unguarded bare `videojs` reference in `generateCaptions`

`generateCaptions(videoSrc, videoId)` (`python_app/templates/document.html:8052-8080`) calls, inside the `fetch(...).then(...)` success branch:

```js
var player = videojs.getPlayer(videoId);   // document.html:8067 — bare, unguarded
```

This dereferences the global `videojs` **without** the `if (window.videojs)` guard used at every other Video.js call site in the codebase (`document.html:5454, 5499, 5517, 7632`; `s3_publish.py:546, 626, 1447`). When `videojs` is not defined as a global on the page, this throws a `ReferenceError: videojs is not defined`.

Because the throw happens inside a promise `.then()`, it is caught by the function's `.catch(function(err){ statusEl.textContent = 'Error: ' + err.message; })` (`document.html:8078`) and surfaced as the exact on-screen text **"Error: videojs is not defined"** in the `#<videoId>-caption-status` span.

### RC-2: The caption feature is editor-only and cannot work on the published (OCI) page

The whole feature is built for the running Flask editor, not the static published output:

- **Server route exists only in the app.** `POST /api/generate-captions/<path>` (`python_app/app.py:2077-2107`) runs `faster-whisper` (`_generate_vtt_captions`, `app.py:2110`). The ML stack is intentionally kept in the worker/single-VM image, and the route exists only in the running Flask process. A published page on OCI object storage is static — there is no server there, so the `POST` can never succeed (and `generateCaptions` builds an `/output/<job>/...` path that does not match the OCI object layout anyway).
- **The function is stripped from published HTML.** `_strip_editor_ui` (`python_app/s3_publish.py:190-236`) deletes everything from `<!-- Formula Input Dialog -->` (`document.html:3574`) through `</body>`, which removes the editor `<script>` (spanning `document.html:4172-8837`) that defines `generateCaptions`. The published learner runtime (`LEARNER_RUNTIME_SCRIPT` / `READER_SHELL_SCRIPT`) contains **no** caption UI and **guards** all `videojs` use.
- **Video.js head tags are kept.** `_strip_editor_ui` keeps the CDN `<script>`/`<link>` for Video.js (`document.html:22-23`), so `window.videojs` is usually present on a normally published page — but the caption code path that needs it has been severed.

### RC-3: A "Generate Captions" button can reach the published page without a working handler

The reported screenshot shows the button on a published-looking page. A "Generate Captions" button/status span that was persisted into the saved **document body** (before the strip boundary at `document.html:3574`) survives publishing, while the `generateCaptions` function (after the boundary) does not. The button's `onclick="generateCaptions(...)"` then references an absent function, or any surviving caption code hits the unguarded `videojs.getPlayer` (RC-1). Either way the learner sees a failing button for a feature that cannot run in that context.

**Net:** RC-1 is the literal cause of the displayed text and is a one-line defect. RC-2/RC-3 are the reason the button should not be actionable (or present) in the published context at all.

## Assumptions To Confirm During Design

- **A-1:** The reported error is on the **published** page (matches the OCI URL and third-party controls). Editor-side caption generation on the running app is essentially healthy (Video.js loaded, route present, function defined). Confirm whether the same button also appears in the editor popup and whether it works there.
- **A-2:** Whether the intended product behavior is (i) captions are an **editor-only** authoring step whose result (a `.vtt`) is published as a track, or (ii) captions should be generatable by learners. The fix differs: (i) means remove/disable the button in the published view; (ii) would require a server reachable from published pages (out of current architecture).

## Functional Requirements

### FR-1: No raw runtime error from the caption button
- **FR-1.1:** Clicking Generate Captions SHALL never surface a raw JavaScript error such as "videojs is not defined" to the user.
- **FR-1.2:** The `videojs.getPlayer(...)` call in `generateCaptions` SHALL be guarded like every other Video.js reference (`window.videojs && window.videojs.getPlayer`), so a missing Video.js global cannot throw.
- **FR-1.3:** When the player is unavailable, the function SHALL degrade gracefully (e.g. a clear status message), not throw.

### FR-2: The button matches where the feature can actually work
- **FR-2.1:** The Generate Captions button SHALL be actionable only in a context where both the `/api/generate-captions` route and Video.js are available (the running editor app).
- **FR-2.2:** On the published/static learner page — where the route does not exist — the caption button SHALL NOT be presented as an actionable control that fails. It SHALL be either absent or clearly non-actionable, per the design decision under A-2.
- **FR-2.3:** If any "Generate Captions" markup was baked into a saved document body, publishing SHALL NOT leave a live-but-broken button on the learner page (either strip it or neutralise it).

### FR-3: Editor-side caption generation works and reports clearly
- **FR-3.1:** In the editor, generating captions SHALL attach the resulting VTT track to the video when the server returns success.
- **FR-3.2:** Editor-side failures (server error, transcription failure, unavailable model) SHALL be reported as a clear, non-technical status message, not a raw exception string.
- **FR-3.3:** If captions were generated in the editor and saved, the resulting VTT track SHALL be publishable so learners get captions without needing to generate them.

### FR-4: Non-regression
- **FR-4.1:** Video playback in both editor and published popups SHALL be unaffected.
- **FR-4.2:** Other media popups (audio, pptx, h5p, vlab, url, glossary) SHALL be unaffected.
- **FR-4.3:** The existing `window.videojs` guards elsewhere SHALL remain intact.

## Non-Functional Requirements

- **NFR-1 (Clarity):** User-facing caption status messages SHALL be plain-language and SHALL NOT expose stack traces, library names, or internal routes.
- **NFR-2 (Diagnosability):** Genuine failures SHALL still be logged to the console with enough detail for a developer, distinct from the user-facing message.
- **NFR-3 (No new heavy dependency):** The fix SHALL NOT add a client-side captioning/runtime dependency; it reuses the existing Video.js + server route model.

## Out of Scope

- Making caption generation work on the static OCI published page (would require a server reachable from published pages; not part of this fix — see A-2).
- Replacing faster-whisper or changing the transcription pipeline.
- Adding caption editing UI.

## Acceptance Criteria

1. In the editor, clicking Generate Captions on a video with the `/api/generate-captions` route available attaches an English VTT track on success and never throws "videojs is not defined".
2. With Video.js not present on the page, clicking Generate Captions (or the code path that adds the track) does not throw; it degrades to a clear status message.
3. On a published learner page, there is no actionable Generate Captions button that produces "videojs is not defined" (button absent or non-actionable per design).
4. A document that had a Generate Captions button baked into its saved body does not present a live-but-broken button after publishing.
5. Editor-side caption failures show a plain-language message, with developer detail in the console only.
6. Video playback and all other media popups are unaffected in both editor and published views.
7. If captions were generated and saved in the editor, the published video carries the caption track.
