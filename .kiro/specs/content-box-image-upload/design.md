# Design: Content Box Image — Upload, URL Validation, and Sizing

## Approach

All three additions land in one place — the image section of the content box modal — plus a small, targeted server change. The existing `POST /upload-media/<job_dir>` endpoint (`app.py:553`) already handles size limits, extension checks, filename sanitising, overwrite avoidance, and returns a relative `media/<name>` URL, so the client wires into it rather than anything new being built.

The aspect-ratio requirement is met primarily by **markup choice rather than by validation logic**: when the ratio is locked, only a width is written and the height is left to the browser, which makes distortion structurally impossible instead of merely discouraged.

## Change Map

| # | Change | File |
|---|---|---|
| 1 | Source chooser + upload control + dimension fields in the modal | `templates/document.html` (markup ~`:3856-3866`, CSS near `:2283-2292`) |
| 2 | URL validation helper | `templates/document.html` (modal IIFE) |
| 3 | Natural-size probe + ratio-linked dimension model | `templates/document.html` (modal IIFE) |
| 4 | `buildBoxHtml()` emits escaped src/alt and sizing | `templates/document.html:6465-6486` |
| 5 | Round-trip parse on re-edit | `templates/document.html:6420-6450` |
| 6 | Live preview honours source + size | `templates/document.html:8795-8812` |
| 7 | SVG acceptance and image size limit | `app.py:583-589`, `config.py` `UPLOAD_LIMITS` |

## 1. Modal UI

Replace the single input inside `#cbImgWrap` with a two-source layout, reusing the existing `cb-*` classes:

```
[ Upload | URL ]                     <- segmented chooser, radio semantics
  (Upload)  [ Choose image ]  filename.png  [x]
  (URL)     [ https://… image URL          ]
  inline error line
Size   W [ 640 ] x H [ 360 ] px   [lock] Keep aspect ratio
       Original: 1280 x 720        Reset
```

- The chooser is two `role="radio"` buttons in a `role="radiogroup"`, matching the modal's existing button-group idiom (`.cb-style-btn`, `.cb-preset`) rather than introducing a `<select>`.
- The file input is visually hidden and driven by a styled button, consistent with the other media controls.
- The size row and "Original: W x H" line stay hidden until a source resolves, so the form is not cluttered for the common no-image case.
- One inline error element serves upload errors, URL validation errors, and load failures (FR-2.5, FR-1.6).

## 2. URL validation

```
FUNCTION validateImageUrl(raw):
    value <- trim(raw)
    IF value is empty: RETURN (valid: false, reason: EMPTY)

    // Resolve against the document so relative refs are handled uniformly,
    // which also normalises away "?query" and "#fragment" for us.
    TRY parsed <- parseUrl(value, base: document.baseURI)
    CATCH: RETURN (valid: false, reason: MALFORMED)

    IF parsed.scheme NOT IN {http, https}:
        RETURN (valid: false, reason: BAD_SCHEME)

    path <- lowercase(parsed.path)           // query + fragment excluded
    IF path does NOT end with one of {.png, .jpg, .jpeg, .svg}:
        RETURN (valid: false, reason: BAD_EXTENSION)

    RETURN (valid: true)
```

Two details carry the weight:

- **Parsing against `document.baseURI`** handles FR-2.4 for free: `media/x.png` and `//host/x.png` resolve to absolute URLs whose scheme is the page's (`http`/`https`), so relative references pass the scheme check without a special case, while `javascript:` and `data:` do not — a URL parser treats those as their own scheme regardless of base.
- **Testing `parsed.path`, never the raw string**, satisfies FR-2.2. A raw `endsWith('.png')` test rejects `figure.png?v=2`, which this application's own asset URLs can look like.

`data:` deserves explicit mention: a `data:image/svg+xml,…` URL would render in an `<img>`, so rejecting it is a deliberate choice to keep arbitrary inline SVG payloads out of saved documents (FR-2.3, FR-6.3).

## 3. Dimensions and the ratio model

### Probing natural size

```
FUNCTION probeNaturalSize(url, onResult):
    probe <- new Image()
    probe.onload  <- () => onResult(ok: true, w: probe.naturalWidth, h: probe.naturalHeight)
    probe.onerror <- () => onResult(ok: false)
    probe.src <- url
```

Asynchronous and non-blocking (NFR-3). `onerror` is what satisfies FR-2.7 — the only way to know a validated URL actually resolves.

For SVG without an intrinsic size, `naturalWidth/naturalHeight` come back `0` in most browsers (FR-3.8). The model treats a zero or missing dimension as "ratio unknown": the lock is disabled, "Original" reads as unavailable, and width defaults to a fixed fallback.

### State

```
imageState:
    source      : NONE | UPLOAD | URL
    url         : string           // relative for uploads, absolute for URL
    naturalW/H  : number | null
    width/height: number | null    // editor's choice; null = unsized
    ratioLocked : boolean          // default true
```

### Linked editing

```
ON width changed to w:
    IF NOT valid positive number within bounds: mark invalid, RETURN
    width <- w
    IF ratioLocked AND ratio known:
        height <- round(w * naturalH / naturalW)

ON height changed to h:        // symmetric
    IF ratioLocked AND ratio known:
        width <- round(h * naturalW / naturalH)
```

Bounds per FR-3.7: positive integers, upper bound a sane ceiling (4000 px covers any print-resolution figure). Rejected input marks the field invalid and leaves state untouched rather than writing a partial value.

### Markup — how the aspect ratio is actually guaranteed

This is the key decision. Three cases:

| Case | Emitted on the `<img>` |
|---|---|
| Unsized (FR-3.5) | `style="max-width:100%;height:auto;…"` — today's behaviour |
| **Locked ratio** | `width`/`height` attributes = natural size, plus `style="width:<W>px;max-width:100%;height:auto;…"` |
| Unlocked | `style="width:<W>px;height:<H>px;max-width:100%;…"` |

In the locked case **only a width is expressed in CSS**; `height:auto` lets the browser derive the height. The ratio cannot drift, be rounded wrong, or be corrupted by a later edit, because the height is never written down. The `width`/`height` *attributes* still carry the natural size, which gives the browser the intrinsic ratio for layout stability before the image loads.

`max-width:100%` is retained in every case, which is what satisfies FR-3.6 — a 2000 px width still shrinks on a narrow viewport rather than overflowing. In the unlocked case `max-width:100%` without `height:auto` will distort on shrink; that is accepted and is part of why unlocked is opt-in and flagged (FR-3.4).

### Escaping

`buildBoxHtml()` currently interpolates raw values into an HTML string (`document.html:6471-6481`). The fix is an attribute-escape helper applied to `src` and `alt`:

```
FUNCTION escAttr(s): replace & < > " ' with their entities
```

Dimensions are formatted through a number conversion, never passed through as strings (FR-6.2). Note the same builder also interpolates `title` into `aria-label` and the title `<div>`; escaping those is in scope per FR-6.1, since the feature touches this function.

A precedent already exists in the codebase — `s3_publish.py:434` defines exactly this helper for injected learner scripts.

## 4. Round-trip on re-edit

`openModal()` currently restores only the `src` (`document.html:6427`). It gains a parse of the existing `<img>`:

```
FUNCTION readImageFromBox(box):
    img <- box.querySelector('img.content-box-image')
    IF none: RETURN state(source: NONE)

    url <- img.getAttribute('src')
    source <- (url starts with 'media/') ? UPLOAD : URL
    naturalW/H <- img width/height attributes, if present
    width  <- parse px from inline style 'width'
    height <- parse px from inline style 'height'
    ratioLocked <- (height was NOT set in the inline style)
    RETURN state(...)
```

The lock state is recovered from the markup rather than stored in a data attribute: an absent inline `height` *is* the signal that the ratio was locked, which is exactly how the locked case is emitted. That keeps the markup free of editor-only metadata (FR-5.3) and makes pre-existing boxes parse correctly — no dimensions, no upload marker, unsized state (FR-4.3).

Distinguishing upload from URL by the `media/` prefix (FR-4.2) holds while the document is unpublished. After publishing, the src is rewritten to an absolute bucket URL, so a re-opened published document would classify it as URL. That is cosmetic — the image still loads and still saves correctly — and is called out in the risk table rather than solved with extra metadata.

## 5. Server changes

Both are small and both are decisions flagged in the requirements:

- **SVG (OD-1):** add `.svg` to the image extension allowlist (`app.py:584`) **only together with** a sanitisation step that strips `<script>`, `on*` event handlers, `<foreignObject>`, and external references from the uploaded file. Without sanitisation, do not add it — the file is served from the application's own origin and the public media bucket, where direct navigation executes it (FR-6.4). URL-referenced SVG needs no server change.
- **Size limit (OD-2):** raise `UPLOAD_LIMITS["image"]` from 1 MB. The client should also pre-check `file.size` against the same number to avoid a pointless round trip, which means the limit needs to be readable by the client — simplest is to render it into the template rather than hard-coding it in two places.

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Unsanitised SVG upload becomes stored XSS on your domain and in the public bucket | **High** | OD-1: sanitise, or don't accept SVG uploads. `<img>`-only rendering (FR-6.3) protects the document but not direct navigation to the file |
| Existing unescaped interpolation in `buildBoxHtml` | **High** | Fixed as part of this change (FR-6.1); covered by AC-14 |
| `data:`/`javascript:` URL accepted | Medium | Scheme allowlist after URL parsing, not a string prefix test |
| Naive `endsWith` rejects legitimate `?query` URLs | Medium | Validate `parsed.path` only |
| Sized image overflows on mobile | Medium | `max-width:100%` retained in all cases |
| Upload succeeds but editor cancels the modal | Low-Medium | Orphan file in `media/`; harmless and published only if referenced. Cleaning it up would need a delete endpoint — out of scope, noted |
| Published box re-opens classified as URL not upload | Low | Cosmetic only; image loads and saves correctly |
| SVG with no intrinsic size breaks the ratio maths | Low | Zero/absent natural size disables the lock and applies a default width |
| 1 MB limit surprises editors | Low | OD-2 |

## Verification Plan

1. **Validation unit-level** — exercise `validateImageUrl` against: the four accepted extensions; `?query` and `#fragment` variants; `.gif`/`.webp`/`.pdf`/no-extension; `javascript:`, `data:`, `file:`; protocol-relative; relative `media/x.png`; malformed input; empty.
2. **Upload happy path** — upload a PNG, confirm it lands in `output/<job>/media/`, the box renders it, and the saved HTML carries a relative `media/…` src.
3. **Upload rejections** — oversized file and a `.gif`; confirm the server message appears inline and the modal keeps its other input.
4. **Ratio locked** — set width, confirm height follows proportionally and the emitted markup has an inline `width` but no inline `height`.
5. **Ratio unlocked** — set both, confirm both appear and the distortion hint is shown.
6. **Responsiveness** — a 2000 px width at a 400 px viewport must not overflow horizontally.
7. **Round trip** — save, re-open, confirm source/dimensions/lock restored; save again and diff the markup for stability.
8. **Legacy box** — open a content box created before this change, confirm it edits and saves.
9. **Escaping** — a title and a URL containing `" onerror=alert(1)` must render as escaped text with no executing markup, in the editor and after publish.
10. **Publish** — publish a document with an uploaded content-box image; confirm the learner page loads it from the media bucket at the chosen size, and that no editor-only attributes leak.
11. **Regression** — run `python_app/tests/` (currently 176 passing, 1 skipped) with `/opt/pdf2html/venv/bin/python -m pytest tests/ -q`.
