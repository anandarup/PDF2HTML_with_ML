# Bug Fix Requirements: H5P Activity Opens as a Link Instead of Embedding in the Popup

## Bug Summary

On a published chapter, clicking an H5P activity opens a modal titled **"H5p"** whose body contains only a single hyperlink, **"Open activity"**, instead of rendering the interactive H5P activity inside the popup.

**Reported on:** a published, rendered chapter served from OCI object storage
(`objectstorage.ap-mumbai-1.oraclecloud.com/.../itb-rendered-html/.../97d9c0ff_lemh105.html`).

**Reported as:** "H5P is not opening when added in the toolbar — it should open in the popup."

## Expected vs Actual

| | Behavior |
|---|---|
| **Expected** | Clicking the H5P activity opens the popup and the activity loads and is usable **inside** the popup (embedded player). |
| **Actual** | The popup opens with title "H5p" and shows only an "Open activity" hyperlink. The activity is not embedded. |

## Terminology Note — two H5P entry points

The word "toolbar" in the report is imprecise, so this spec pins down both H5P paths and states which one produces the reported symptom.

1. **Insert-menu H5P** (`#btnInsertH5P` → `#h5pInsertModal` → `insertH5PContent`, `python_app/templates/document.html`). Produces an **inline** element `<div class="h5p-inline-container" data-h5p-src="…">` that renders in the page body. It is **not** click-to-popup.
2. **Per-section media chip** (`<button class="media-icon" data-media-type="h5p" data-media-src="media/<folder>">`, `document.html:7309+`). On the published page, clicking it opens the `#mediaPopup` modal. **This is the path that shows the "Open activity" link.**

The exact reported symptom — popup **title "H5p"** plus body link **"Open activity"** — is produced by the published-page popup scripts injected by `python_app/s3_publish.py`, and corresponds to the **media-chip** path. See Root Cause.

**Scope decision (confirm with requester):** The primary fix targets the published-popup H5P embed (media-chip path). Because both paths share the same `h5p-standalone` player, the CDN, and the uploaded package, the fix must also ensure the **inline** Insert-menu H5P embeds correctly in the published view. Any editor-side (pre-publish) preview regressions are in scope only where they share the same root cause.

## Root Cause

The published learner page has **no server-side H5P runtime**. Embedding is entirely client-side via the `h5p-standalone` player loaded from the unpkg CDN. `python_app/s3_publish.py` injects two scripts before `</body>`: a **learner runtime** and a **reader shell**. Both can populate `#mediaPopup`, and both fall back to a plain link **only when the player fails to mount**.

### RC-1: the "Open activity" link is a failure fallback, not the design

- `python_app/s3_publish.py` `build('h5p', src)` (reader-shell script) ends with a terminal fallback that sets the popup body to `…<a href="…">Open activity</a>…`. This is the **only** source of the literal text "Open activity".
- The sibling learner-runtime path, `h5pMount` → `h5pFail`, uses the text **"Open activity files"** (plural).
- Both are reached **only** when `window.H5PStandalone` is missing **or** the `new H5PStandalone.H5P(...)` constructor throws / the package libraries fail to resolve. So the bug is: **the player is not mounting on the published page**, and the code degrades to a link.

### RC-2: two competing popup handlers with divergent titles

- The **learner-runtime** per-button handler titles the popup by `type.charAt(0).toUpperCase()+type.slice(1)` → for `type="h5p"` this is exactly **"H5p"**, and mounts via the full-featured `h5pMount`.
- The **reader-shell** delegated document-level handler titles via a map `MI={…h5p:'Activity'…}` → **"Activity"**, and builds the body via `build('h5p')` whose fallback text is **"Open activity"**.
- The reported combination (title **"H5p"** + link **"Open activity"**) is a **cross** of the two handlers: the learner-runtime titled the popup, but the "Open activity" body text originates in the reader-shell `build()`. This indicates the two handlers interact/compete, and at least one produces the link. The duplication and its guard (`if popup already visible, return`) must be reconciled so exactly one handler renders, and it must be the one that actually attempts a full embed.

### RC-3: candidate reasons the player fails to mount on the published page

The fix must determine which of these actually applies to the reported document, and address it. All are consistent with "link fallback shown":

- **RC-3a — CDN unavailable / blocked.** `window.H5PStandalone` never exists because `https://unpkg.com/h5p-standalone@3.8.0/dist/main.bundle.js` (and the `frame.bundle.js` / `styles/h5p.css` referenced at mount) did not load (network policy, offline, CDN outage, CSP).
- **RC-3b — package folder not reachable at `<src>/h5p.json`.** On OCI the media-chip `data-media-src` must resolve to the uploaded, unpacked package directory. If the H5P bundle was not uploaded to the media bucket, or the URL does not resolve to `<folder>/h5p.json`, the constructor throws and the code falls back. (`_find_h5p_roots` uploads bundle directories whole; verify it fires for this document and that the chip `src` was rewritten to the bucket URL.)
- **RC-3c — relative asset resolution inside the iframe.** Packages declare `embedTypes:["iframe"]`; asset URLs must be absolutised (`h5pAbs`/`h5pAbsSrc`). If `src` is not absolutised on the published page, `h5p.json` may load but libraries/content break, and `h5pWatch` reports libraries "could not be resolved," falling back to a link.
- **RC-3d — package library graph incomplete.** If the uploaded package's `preloadedDependencies` cannot be resolved at runtime, `h5pWatch` triggers `h5pFail`. Server-side `normalize_h5p_package` is meant to catch this at upload; verify it ran for this document.

### RC-4: silent, hard-to-diagnose failure in production

Failures currently surface only as a console error plus the link fallback. There is no learner-visible or operator-visible signal distinguishing "CDN blocked" from "package missing" from "libraries unresolved," which is why the report cannot yet name the cause. The fix should make the failure mode diagnosable.

## Functional Requirements

### FR-1: Embed the H5P activity in the popup when it can be played
- **FR-1.1:** On a published chapter, clicking an H5P media chip SHALL open the popup and mount the interactive activity **inside** the popup body, not present a bare link, whenever the package and player are available.
- **FR-1.2:** The embedded activity SHALL be interactive (the same `h5p-standalone` iframe experience already intended by the code and comments).
- **FR-1.3:** An inline Insert-menu H5P (`.h5p-inline-container[data-h5p-src]`) SHALL likewise render its embedded player in the published view when the package and player are available.
- **FR-1.4:** A single pre-built H5P **page** (`.html`) referenced as `src` SHALL continue to be embedded via an iframe, as the current code intends.

### FR-2: One popup handler, correct and consistent
- **FR-2.1:** Exactly one code path SHALL populate `#mediaPopup` for an H5P click; the learner-runtime and reader-shell handlers SHALL NOT both attempt to build the same popup body such that a full-embed attempt is replaced by a link.
- **FR-2.2:** Whichever handler renders SHALL use the full-featured mount logic (equivalent to `h5pMount` + `h5pWatch`), not a reduced `build()` that falls back to a link before genuinely attempting the embed.
- **FR-2.3:** The popup title for an H5P activity SHALL be consistent and human-readable (a single agreed label), not depend on which handler happened to run.

### FR-3: Correct, resolvable package URL on the published page
- **FR-3.1:** For a published document, the H5P `src` (both `data-media-src` on chips and `data-h5p-src` on inline containers) SHALL resolve to the uploaded package directory such that `<src>/h5p.json` is fetchable in the browser.
- **FR-3.2:** The `src` passed to the player SHALL be absolutised so that iframe-relative asset URLs inside the package resolve (RC-3c).
- **FR-3.3:** Under `OUTPUT_BACKEND=oci`, the H5P package directory SHALL be uploaded to object storage intact (all manifests, JS, CSS, and content assets) and the published `src` SHALL point at the bucket location (RC-3b).

### FR-4: Player runtime availability
- **FR-4.1:** The published page SHALL load the `h5p-standalone` player runtime reliably enough that a correctly packaged activity embeds. If CDN reachability is the confirmed cause (RC-3a), the fix SHALL make the runtime available to the published page by an approach agreed in design (e.g. verified CDN with integrity/fallback, or self-hosting the bundle alongside the output), rather than depending on an external host that the target environment blocks.
- **FR-4.2:** The runtime reference SHALL survive publishing (it must not be stripped by `_strip_editor_ui`).

### FR-5: Honest, diagnosable failure state
- **FR-5.1:** When an activity genuinely cannot be embedded (package missing, player unavailable, libraries unresolved), the popup SHALL show a clear, non-technical message rather than only a bare "Open activity" link.
- **FR-5.2:** The failure SHALL emit a distinguishable diagnostic (console and, where server-side, structured log) that identifies the cause category (runtime missing vs package unreachable vs libraries unresolved), so the mode is diagnosable without guessing.
- **FR-5.3:** Any fallback link, if retained as a secondary affordance, SHALL be accompanied by the FR-5.1 message and SHALL NOT be the primary experience when embedding is possible.
- **FR-5.4:** Failure messages SHALL NOT expose bucket names, stack traces, or internal paths.

### FR-6: Backward compatibility and non-regression
- **FR-6.1:** Non-H5P media popups (video, audio, pptx, vlab, url, glossary) SHALL be unaffected.
- **FR-6.2:** Already-published documents that currently work SHALL NOT regress; if a repair of existing published pages is required, it SHALL be called out explicitly (published pages are baked HTML and are not re-rendered — see the split-view bugfix spec for the same constraint).
- **FR-6.3:** The editor (pre-publish) H5P preview SHALL continue to work.
- **FR-6.4:** The server-side upload/extract/validation flow (`_extract_h5p` → `normalize_h5p_package`, `_find_h5p_roots`) SHALL be preserved unless design identifies a specific defect in it.

## Non-Functional Requirements

- **NFR-1 (Reliability):** Embedding SHALL not depend on a single un-fallback-able external host if that host is the confirmed failure cause; the chosen approach SHALL be resilient in the target deployment (DIKSHA / OCI).
- **NFR-2 (Diagnosability):** The cause of an embed failure SHALL be determinable from logs/console for a given document without source-diving.
- **NFR-3 (No new heavy dependency):** Prefer reusing the existing `h5p-standalone` player; do not bundle a second H5P runtime. Self-hosting the existing bundle is acceptable if design selects it.
- **NFR-4 (Safety):** Any change to published output SHALL preserve the invariant that publishing does not overwrite a learner page with editor chrome, and SHALL back up before patching any existing baked HTML.

## Out of Scope

- Authoring new H5P content types or changing the H5P authoring/upload UX beyond what the fix requires.
- Replacing `h5p-standalone` with a different player.
- Server-side rendering of H5P (there is deliberately no server runtime; embedding stays client-side).

## Assumptions To Confirm During Design

- **A-1:** The reported document's H5P was added via the **per-section media chip**, matching the "Open activity" symptom. Confirm against the actual published HTML for `97d9c0ff_lemh105`.
- **A-2:** Whether the confirmed cause is CDN reachability (RC-3a) vs package hosting/URL (RC-3b/c) vs library graph (RC-3d) — design must determine this from the reported document before selecting the fix, since the remedies differ.

## Acceptance Criteria

1. On a freshly published chapter containing a valid H5P activity added via the media chip, clicking the chip opens the popup and the activity **renders and is interactive inside the popup** — no "Open activity" link is shown.
2. The popup for an H5P activity has a single, consistent, human-readable title (not sometimes "H5p" and sometimes "Activity"), and exactly one handler renders its body.
3. An inline Insert-menu H5P activity renders its embedded player in the published view.
4. A single pre-built `.html` H5P page still embeds via iframe.
5. Under `OUTPUT_BACKEND=oci`, the H5P package is reachable at `<src>/h5p.json` from the published page and the activity embeds; asset URLs inside the package resolve.
6. When the player runtime is genuinely unavailable, the popup shows the FR-5.1 message and emits a cause-identifying diagnostic — not a bare link with no explanation.
7. Video, audio, pptx, vlab, url, and glossary popups are unchanged.
8. The confirmed root cause for the reported document (`97d9c0ff_lemh105`) is identified and addressed, and that specific document (or a re-published equivalent) embeds its H5P activity in the popup.
9. If existing published pages need repair, a documented, `--dry-run`-capable, backup-first mechanism is provided and does not overwrite published learner pages with editor chrome.
