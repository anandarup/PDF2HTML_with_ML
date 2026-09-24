# Requirements: Content Box Image — Upload, URL Validation, and Sizing

## Overview

The "Insert content box" modal currently offers a single way to add an image: paste a URL into `#cbImageInput` (`python_app/templates/document.html:3863`). There is no file upload, no validation that the URL is actually an image, and no way to control the rendered size — the produced markup hard-codes `max-width:100%` and nothing else (`document.html:6479-6481`).

This feature adds three things to that one field:

1. **Upload** a local image file, as an alternative to pasting a URL.
2. **Validate** a pasted URL so it points at a PNG, JPG, JPEG, or SVG.
3. **Size** the image, with the original aspect ratio respected by default.

## Current State

| Concern | Today |
|---|---|
| Image source | URL only, `<input type="url" id="cbImageInput">` (`document.html:3863`) |
| Markup produced | `<img class="content-box-image" src="…" alt="…" style="max-width:100%;border-radius:6px;margin:0.5rem 0;">` (`document.html:6479-6481`) |
| Round-trip on re-edit | `imageInput.value = imgEl.getAttribute('src')` (`document.html:6427`) |
| Live preview | `pvImg.src = imageInput.value` (`document.html:8808-8810`) |
| Validation | none beyond the browser's `type="url"` |
| Upload endpoint | `POST /upload-media/<job_dir>` already exists (`app.py:553`) — used by other media, not by the content box |

The upload endpoint already does most of what is needed: per-type size limits, extension checks, filename sanitising, overwrite avoidance, and it returns a relative `media/<name>` URL. For `type=image` it currently allows `.png`, `.jpg`, `.jpeg` with a **1 MB** limit (`app.py:583-589`, `config.py` `UPLOAD_LIMITS`).

## Functional Requirements

### FR-1: Upload an image file

- **FR-1.1:** The image section of the modal SHALL let the editor choose between two sources: uploading a file and entering a URL.
- **FR-1.2:** Upload SHALL reuse the existing `POST /upload-media/<job_dir>` endpoint with `type=image`. No new endpoint SHALL be created.
- **FR-1.3:** The job directory SHALL be derived from the current document URL, consistent with how other editor features address the job.
- **FR-1.4:** On success the returned relative URL (`media/<filename>`) SHALL become the image source, and SHALL be stored in the markup as that relative path so the publish step can rewrite it to a bucket URL.
- **FR-1.5:** While the upload is in flight the editor SHALL see a progress or busy state, and the modal's confirm action SHALL be prevented from completing with a half-finished upload.
- **FR-1.6:** Upload failures SHALL surface the server's message (too large, wrong type, path error) in the modal, near the control, without closing the modal or losing the editor's other input.
- **FR-1.7:** Switching between the two sources SHALL NOT silently retain a stale value from the other; whichever source is active is the one that applies.
- **FR-1.8:** An uploaded image SHALL be removable, returning the box to having no image.

### FR-2: URL validation

- **FR-2.1:** A pasted URL SHALL be accepted only when its path ends in `.png`, `.jpg`, `.jpeg`, or `.svg`, case-insensitively.
- **FR-2.2:** The extension SHALL be tested against the URL's **path only**, ignoring query string and fragment, so `…/figure.png?v=2` and `…/figure.png#fig1` are accepted. A naive end-of-string test would wrongly reject these; the application's own OCI asset URLs can carry query parameters.
- **FR-2.3:** Only `http:` and `https:` URLs SHALL be accepted. `javascript:`, `data:`, `file:`, and other schemes SHALL be rejected. This is a security control, not a convenience check — the value is interpolated into an HTML attribute.
- **FR-2.4:** Protocol-relative (`//host/x.png`) and same-origin relative URLs (`media/x.png`, `images/x.png`) SHALL be accepted, since the editor legitimately references assets already in the job.
- **FR-2.5:** Rejection SHALL produce a specific, non-technical message naming the accepted formats, shown inline and not as a browser alert.
- **FR-2.6:** Validation SHALL run before the box is inserted or updated, and an invalid URL SHALL block that action rather than producing a broken image.
- **FR-2.7:** A URL that passes validation but fails to load SHALL be reported to the editor, since an extension check cannot prove the resource exists.

### FR-3: Dimensions and aspect ratio

- **FR-3.1:** Once an image source resolves, the modal SHALL read the image's natural width and height and display them, so the editor knows the original size.
- **FR-3.2:** The editor SHALL be able to set the displayed width and height.
- **FR-3.3:** Aspect ratio SHALL be locked by default: editing one dimension recomputes the other from the natural ratio, so the default path cannot distort the image.
- **FR-3.4:** The editor SHALL be able to unlock the ratio and set both dimensions independently. When unlocked, the UI SHALL indicate that the image may be distorted.
- **FR-3.5:** Leaving the dimensions untouched SHALL preserve today's behaviour — a responsive image capped at the container width.
- **FR-3.6:** A sized image SHALL remain responsive: it SHALL NOT overflow its container on narrow screens, even when the chosen width exceeds the available space.
- **FR-3.7:** Dimension values SHALL be validated as positive numbers within sane bounds, and non-numeric or out-of-range input SHALL be rejected without corrupting the markup.
- **FR-3.8:** Images with no intrinsic size (common for SVG) SHALL be handled without error: the ratio lock SHALL degrade gracefully and a sensible default width SHALL apply.

### FR-4: Round-trip on re-edit

- **FR-4.1:** Re-opening an existing content box (double-click) SHALL restore the current image source, its dimensions, and the lock state, so a second edit does not reset the editor's sizing.
- **FR-4.2:** An image added by upload SHALL be distinguishable from one added by URL when the box is re-opened, so the correct source control is shown.
- **FR-4.3:** Boxes created before this feature SHALL continue to open and save correctly, with no image dimensions and no upload metadata present.
- **FR-4.4:** The live preview SHALL reflect the chosen source and the chosen dimensions.

### FR-5: Publishing and the learner view

- **FR-5.1:** Uploaded images SHALL be published with the document. `publish_document` already walks the job directory and rewrites relative media paths to bucket URLs in a single pass (`s3_publish.py:1435-1505`), so a relative `media/<name>` source is carried automatically — this SHALL be verified, not assumed.
- **FR-5.2:** Dimensions SHALL survive publishing and render identically in the learner view.
- **FR-5.3:** No editor-only attribute or control SHALL leak into the published HTML.

### FR-6: Security

- **FR-6.1:** The image source and the `alt` text SHALL be attribute-escaped when written into markup. The current builder interpolates both the URL and the title into an HTML string unescaped (`document.html:6471-6481`), so a value containing a double quote can break out of the attribute and inject markup. This SHALL be fixed as part of this change, since the feature adds new paths into the same builder.
- **FR-6.2:** Dimension values SHALL be written as numbers, never as pass-through strings, so they cannot carry markup or CSS injection.
- **FR-6.3:** SVG SHALL always be rendered through an `<img>` element and SHALL NEVER be inlined into the document. Browsers do not execute scripts in an `<img>`-referenced SVG; inlining removes that protection.
- **FR-6.4:** If SVG upload is enabled (see Open Decisions), the stored file SHALL be treated as untrusted active content: it is served from the application's own domain and the media bucket, where opening it directly would execute any embedded script.

## Non-Functional Requirements

- **NFR-1:** The modal SHALL remain usable with the keyboard; the source chooser, dimension fields, and lock toggle SHALL be reachable and labelled.
- **NFR-2:** The added controls SHALL follow the existing `cb-*` design language already used in this modal.
- **NFR-3:** Reading natural dimensions SHALL not block the modal; a slow or unreachable image SHALL leave the rest of the form usable.
- **NFR-4:** No new runtime dependency, client or server.

## Open Decisions

These need a call before implementation; each has a recommendation.

- **OD-1: SVG upload.** The requirement names SVG only for the URL case. The upload endpoint currently allows `.png`, `.jpg`, `.jpeg` (`app.py:584`). SVG is an active-content format: uploaded into `media/` it is published to the public media bucket and served from your domain, so a learner navigating directly to the file would execute any script inside it. Rendering via `<img>` is safe, but the file remains directly reachable.
  *Recommendation:* allow SVG by URL as asked; for upload, either keep SVG out, or allow it and sanitise server-side (strip `<script>`, event handlers, `<foreignObject>`, external references). Do not allow unsanitised SVG upload.
- **OD-2: Image size limit.** `UPLOAD_LIMITS["image"]` is **1 MB**, which is small for a textbook figure and likely to be hit immediately.
  *Recommendation:* raise it (5 MB is a reasonable default) and make sure the error message states the limit. Note the limit is enforced server-side only, so the client should pre-check to avoid a wasted upload.

## Acceptance Criteria

1. The image section of the modal offers both an upload control and a URL field, and only the active source applies.
2. Uploading a PNG stores it under the job's `media/` directory and the inserted box renders it.
3. An upload larger than the configured limit is rejected with a message naming the limit; the modal stays open with other input intact.
4. URLs ending in `.png`, `.jpg`, `.jpeg`, `.svg` are accepted, including with `?query` and `#fragment`.
5. A URL ending in `.gif`, `.webp`, `.pdf`, or no extension is rejected with a message naming the accepted formats.
6. A `javascript:` or `data:` URL is rejected.
7. A relative `media/x.png` or `images/x.png` URL is accepted.
8. After a source resolves, the modal shows the image's natural dimensions.
9. With the ratio locked, changing width updates height proportionally, and vice versa.
10. With the ratio unlocked, both dimensions can be set independently and the UI flags possible distortion.
11. A width larger than the container does not cause horizontal overflow on a narrow viewport.
12. A box with a sized image re-opens with source, dimensions, and lock state restored, and saving twice does not change the result.
13. A content box created before this feature still opens, edits, and saves.
14. A URL or title containing `" onerror=alert(1)` produces escaped text, no executing markup, in both the editor and published HTML.
15. Publishing a document containing an uploaded content-box image yields a learner page where the image loads from the media bucket at the chosen size.
