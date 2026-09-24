# Tasks: Content Box Image — Upload, URL Validation, and Sizing

Task 0 is a decision gate. Tasks 1–3 are self-contained client pieces. Task 4 is the security fix that everything else writes through. Tasks 5–7 wire it together. Task 8 verifies.

All work is in `python_app/templates/document.html` unless stated. Interpreter: `/opt/pdf2html/venv/bin/python`, run from `/opt/pdf2html/app/python_app`.

---

## Task 0: Settle the two open decisions

- [ ] 0.1: **OD-1, SVG upload.** Decide one of: (a) URL-only SVG, upload stays PNG/JPG — no server change; (b) allow SVG upload **with** server-side sanitisation. Do not ship (c) unsanitised SVG upload — the file is served from your own origin and the public media bucket, where direct navigation executes embedded script.
- [ ] 0.2: **OD-2, image size limit.** Confirm whether `UPLOAD_LIMITS["image"]` stays at 1 MB or is raised. If raised, decide the value and how the client learns it (render into the template rather than duplicating the constant).
- [ ] 0.3: Record both decisions in the spec so the implementation does not re-litigate them.

---

## Task 1: URL validation helper

**Satisfies:** FR-2

- [ ] 1.1: Add `validateImageUrl(raw)` inside the content box modal IIFE, returning a result object with a validity flag and a reason code (`EMPTY`, `MALFORMED`, `BAD_SCHEME`, `BAD_EXTENSION`).
- [ ] 1.2: Parse with the URL API against `document.baseURI` rather than testing the raw string. This resolves relative and protocol-relative references (FR-2.4) and normalises query/fragment away in one step.
- [ ] 1.3: Allow only `http:`/`https:` after parsing (FR-2.3). A string-prefix check is not sufficient; parse first, then inspect the scheme.
- [ ] 1.4: Test the extension against the parsed **path**, lowercased, against `.png`/`.jpg`/`.jpeg`/`.svg` (FR-2.1, FR-2.2). Do not test the raw string — that rejects `figure.png?v=2`.
- [ ] 1.5: Map reason codes to non-technical messages naming the accepted formats (FR-2.5).

**Verify:** in the browser console, run the helper against the full matrix in design step 1 — four good extensions, `?query`/`#fragment`, `.gif`/`.webp`/`.pdf`/no-extension, `javascript:`/`data:`/`file:`, protocol-relative, relative `media/x.png`, malformed, empty.

---

## Task 2: Natural-size probe and the dimension model

**Satisfies:** FR-3.1, FR-3.3, FR-3.7, FR-3.8

- [ ] 2.1: Add `probeNaturalSize(url, onResult)` using an off-document `Image()` with `onload`/`onerror`. Must not block the modal (NFR-3).
- [ ] 2.2: Introduce the `imageState` object from the design: `source`, `url`, `naturalW/H`, `width/height`, `ratioLocked` (default `true`).
- [ ] 2.3: Treat a zero or missing natural dimension as "ratio unknown": disable the lock, show "Original" as unavailable, apply a default width (FR-3.8). SVGs commonly report `0`.
- [ ] 2.4: Implement the linked width/height handlers — when locked and the ratio is known, editing one derives the other by rounding.
- [ ] 2.5: Validate dimension input as positive integers with an upper bound (4000). Invalid input marks the field and leaves state unchanged; it must never write a partial value (FR-3.7).
- [ ] 2.6: Use `onerror` from the probe to report a validated-but-unreachable URL (FR-2.7).

**Verify:** probe a real PNG and confirm reported natural size matches the file; probe a 404 URL and confirm the load-failure message; probe an SVG with no `width`/`height` and confirm the lock disables cleanly with no console error.

---

## Task 3: Modal markup and styling

**Satisfies:** FR-1.1, FR-1.8, FR-3.2, FR-3.4, NFR-1, NFR-2

- [ ] 3.1: Replace the contents of `#cbImgWrap` (`:3862-3864`) with the two-source layout: a `role="radiogroup"` chooser of two buttons, an upload row (visually hidden file input + styled trigger + filename + remove), the existing URL input, and one shared inline error element.
- [ ] 3.2: Add the size row — width and height number inputs, a lock toggle labelled "Keep aspect ratio", an "Original: W x H" readout, and a reset. Hidden until a source resolves.
- [ ] 3.3: Style with new `cb-*` rules beside the existing ones (`:2283-2292`). Additive only; do not modify existing modal CSS.
- [ ] 3.4: Label every control and give the lock toggle a pressed state; the chooser must use radio semantics, not just styling (NFR-1).
- [ ] 3.5: Show the distortion hint whenever the lock is off (FR-3.4).
- [ ] 3.6: Wire the remove control to clear the image back to no-image state (FR-1.8).
- [ ] 3.7: Switching source must make the inactive source's value inapplicable, so a stale URL cannot survive behind an upload or vice versa (FR-1.7).

---

## Task 4: Escape attributes in `buildBoxHtml`

**Satisfies:** FR-6.1, FR-6.2
**Do this before Task 5**, since the sizing work writes through this function.

- [ ] 4.1: Add an `escAttr(s)` helper escaping `&`, `<`, `>`, `"`, `'`. `s3_publish.py:434` already contains this pattern — match it.
- [ ] 4.2: Apply it to the image `src` and `alt` in `buildBoxHtml` (`:6479-6481`).
- [ ] 4.3: Apply it to `title` where it is interpolated into `aria-label` and the title `<div>` (`:6472-6478`). These are pre-existing injection points in the function being modified; leaving them would be knowingly shipping a hole.
- [ ] 4.4: Format dimensions through a numeric conversion, never string pass-through.

**Verify:** build a box with title and URL both containing `" onerror=alert(1)` and confirm the generated HTML contains escaped entities and no live attribute.

---

## Task 5: Emit sizing in `buildBoxHtml`

**Satisfies:** FR-3.5, FR-3.6, and the ratio guarantee

- [ ] 5.1: Unsized → keep today's exact output: `max-width:100%` plus the existing radius/margin (FR-3.5).
- [ ] 5.2: Locked ratio → emit `width`/`height` **attributes** carrying the natural size, and an inline style with `width:<W>px; max-width:100%; height:auto`. **Do not emit an inline height.** Letting the browser derive the height is what makes distortion structurally impossible and is also how Task 7 recovers the lock state.
- [ ] 5.3: Unlocked → emit inline `width:<W>px; height:<H>px; max-width:100%`.
- [ ] 5.4: Retain `max-width:100%` in every branch (FR-3.6).
- [ ] 5.5: Emit no editor-only attributes (FR-5.3).

**Verify:** inspect generated markup for all three cases; confirm the locked case has no inline `height`.

---

## Task 6: Upload wiring

**Satisfies:** FR-1.2 – FR-1.6

- [ ] 6.1: On file selection, POST `FormData` with `file` and `type=image` to `/upload-media/<job_dir>` (`app.py:553`). Create no new endpoint.
- [ ] 6.2: Derive `job_dir` from the document URL the same way the existing split-view code does (second-to-last path segment of `/output/<job_dir>/<file>.html`), and encode it consistently — the route already `unquote`s it server-side.
- [ ] 6.3: Pre-check `file.size` against the configured image limit and reject locally with the same wording, to avoid a doomed upload (depends on Task 0.2).
- [ ] 6.4: Show a busy state during the request and block the modal's confirm action while in flight (FR-1.5).
- [ ] 6.5: On success, take `url` from the response (relative `media/<name>`) into `imageState`, store it as-is so publish can rewrite it (FR-1.4), then run Task 2's probe to populate natural size.
- [ ] 6.6: On failure, render the server's `error` string in the inline error element; keep the modal open and other input intact (FR-1.6).

**Verify:** upload a PNG and confirm the file appears in `output/<job>/media/` and the box renders; upload an oversized file and a `.gif` and confirm the server messages surface inline.

---

## Task 7: Round-trip and live preview

**Satisfies:** FR-4

- [ ] 7.1: Add `readImageFromBox(box)` parsing the existing `<img class="content-box-image">`: `src`, natural size from the `width`/`height` attributes, chosen size from the inline style.
- [ ] 7.2: Recover `ratioLocked` as "the inline style has no `height`" — the direct consequence of Task 5.2. Do not add a data attribute for this.
- [ ] 7.3: Classify `source` as upload when the src starts with `media/`, else URL (FR-4.2). Accept that a published document's rewritten absolute URL classifies as URL; it still loads and saves correctly.
- [ ] 7.4: Replace the single-line restore at `:6427` with this parse, and make sure a box with no image yields a clean no-image state (FR-4.3).
- [ ] 7.5: Update the live preview (`:8795-8812`) to reflect the active source and the chosen dimensions (FR-4.4).
- [ ] 7.6: Confirm the `hasImg` check at `:8865` still reports correctly for both sources.

**Verify:** save a sized box, re-open, confirm source/dimensions/lock restored; save again and diff the markup for stability; open a pre-feature content box and confirm it edits and saves.

---

## Task 8: Server changes and verification

- [ ] 8.1: Apply the Task 0 decisions: SVG allowlist plus sanitisation if chosen (`app.py:583-589`), and the image limit (`config.py` `UPLOAD_LIMITS`). If SVG upload is declined, make the rejection message say PNG/JPG explicitly so it does not read as a bug.
- [ ] 8.2: If the limit changed, expose it to the client for Task 6.3 rather than hard-coding the number twice.
- [ ] 8.3: **Escaping** — title and URL with `" onerror=alert(1)`: escaped text, no live markup, in editor and published output (AC-14).
- [ ] 8.4: **Responsiveness** — 2000 px width at a 400 px viewport: no horizontal overflow (AC-11).
- [ ] 8.5: **Publish** — publish a document with an uploaded content-box image; confirm the learner page loads it from the media bucket at the chosen size and no editor-only attributes leak (AC-15, FR-5).
- [ ] 8.6: **Regression** — `/opt/pdf2html/venv/bin/python -m pytest tests/ -q` from `python_app`. Baseline is 176 passed, 1 skipped.
- [ ] 8.7: Walk acceptance criteria 1–15 in `requirements.md` and record the result of each.

**Do not run a real PDF conversion to verify.** This machine holds production OCI credentials; a conversion writes to the live buckets. Use `OCI_UPLOADS_ENABLED=false` if a conversion is genuinely needed.

---

## Definition of Done

- Task 0 decisions recorded; Tasks 1–7 implemented; Task 8 verified.
- All 15 acceptance criteria pass.
- No new endpoint, no new dependency.
- `buildBoxHtml` escapes every interpolated value, including the pre-existing title paths.
- The locked-ratio case emits no inline height.
