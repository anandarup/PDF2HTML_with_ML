# PDF2HTML — API Documentation

**Base URL:** `https://poc-interactivetxtbk.diksha.gov.in`
**Source:** `python_app/app.py`
**Date:** 2026-09-09

> Documents the HTTP endpoints exactly as implemented. All routes are defined with `@app.route` in `app.py`. Authentication is not enforced at the application layer in the current code (server-to-server callers are expected to be restricted by network/firewall).

---

## Endpoint Index

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Upload page (drag-and-drop) |
| POST | `/convert` | Upload a PDF, start background conversion |
| GET | `/convert-status/<job_id>` | Poll conversion progress |
| GET | `/api/lookup/<refId>` | Look up a job by external reference ID |
| GET | `/api/sections/<refId>` | Heading hierarchy by refId |
| GET | `/api/sections-by-path/<job_dir>/<filename>` | Heading hierarchy by path |
| POST | `/api/glossary-highlight` | Highlight glossary terms in HTML |
| POST | `/upload-media/<job_dir>` | Upload media for a document |
| GET | `/output/<job_dir>/<filename>` | Serve rendered HTML/assets |
| PUT | `/output/<job_dir>/<filename>` | Save edited HTML |
| POST | `/publish` | Publish document to OCI for learners |
| POST | `/export-cms` | Export to Strapi or WordPress |
| GET | `/api/label-info/<term>` | Diagram label description (Wikipedia) |
| POST | `/api/make-interactive` | Detect diagram labels |
| POST | `/api/progress` | Save video playback progress |
| GET | `/api/progress/<learner_id>/<video_src>` | Get saved video progress |
| POST | `/api/generate-captions/<video_path>` | Generate WebVTT captions (Whisper) |

---

## 1. `POST /convert`

Uploads a PDF and starts conversion in a background thread. Returns immediately.

- **Content-Type:** `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `pdf` | file | yes | Must end in `.pdf`; max 100 MB |
| `ref_id` | string | no | External reference ID from a parent app |

**Success — 202**
```json
{ "job_id": "a1b2c3d4" }
```

**Errors:** `400` — `No PDF file provided` / `No file selected` / `Only PDF files are accepted`.

---

## 2. `GET /convert-status/<job_id>`

Polls a conversion job.

**200**
```json
{
  "status": "processing | done | error",
  "stage": "queued | extracting | building | done | error",
  "detail": "human-readable message",
  "result": {
    "success": true,
    "title": "Chapter 5 — Life Processes",
    "html_url": "/output/<job_dir>/<file>.html",
    "page_count": 21,
    "image_count": 46,
    "file_size": 372136
  },
  "error": null,
  "ref_id": "…",
  "edit_url": "/output/<job_dir>/<file>.html",
  "render_url": null
}
```

**404** — `{ "error": "Unknown job_id" }`

---

## 3. `GET /api/lookup/<refId>`

Primary server-to-server integration endpoint. Finds a job by its `ref_id` and maps status to external states.

- **Validation:** `refId` must match `^[a-zA-Z0-9\-]{8,64}$`.

**200**
```json
{
  "status": "converting | ready-for-edit | published | error",
  "editUrl": "/output/<job_dir>/<file>.html",
  "renderUrl": "https://objectstorage.ap-hyderabad-1.oraclecloud.com/n/<ns>/b/poc-interactivetxtbk1/o/…",
  "title": "Chapter 5 — Life Processes",
  "pageCount": 21,
  "imageCount": 46
}
```

| Status | Meaning | editUrl | renderUrl |
|---|---|---|---|
| `converting` | still processing | null | null |
| `ready-for-edit` | conversion done | set | null |
| `published` | pushed to OCI | set | set |
| `error` | conversion failed | null | null |

**400** — `Invalid refId format` · **404** — `Unknown refId`

---

## 4. `GET /api/sections/<refId>`

Returns the heading hierarchy (h1–h3) parsed from the document body, for building a TOC/sidebar.

**200**
```json
{
  "title": "Chapter 5 — Life Processes",
  "sectionCount": 4,
  "totalHeadings": 9,
  "sections": [
    { "level": 2, "id": "nutrition", "title": "Nutrition",
      "subsections": [ { "level": 3, "id": "autotrophic", "title": "Autotrophic Nutrition" } ] }
  ]
}
```

**400** — `Invalid refId format` · **404** — `Unknown refId` / `Document not ready yet` / `HTML file not found`

### 4a. `GET /api/sections-by-path/<job_dir>/<filename>`
Same output, addressed by path instead of `refId`. `filename` must end in `.html`; `..` in path → `403`.

---

## 5. `POST /api/glossary-highlight`

Wraps glossary terms in accessible `<dfn class="glossary-term">` tooltips.

**Request**
```json
{
  "html": "<p>Photosynthesis occurs in the chloroplast.</p>",
  "glossary": [ { "term": "Photosynthesis", "definition": "…" } ],
  "first_occurrence_only": false,
  "max_highlights_per_term": 0
}
```

**200**
```json
{ "success": true, "html": "…", "css": "/* GLOSSARY_CSS */", "terms_count": 1 }
```

**400** — `No data provided` / `html field is required` / `glossary array is required` · **500** — `Processing failed: …`

---

## 6. `POST /upload-media/<job_dir>`

Uploads a media file into the document's `media/` folder.

- **Content-Type:** `multipart/form-data`

| Field | Type | Notes |
|---|---|---|
| `file` | file | required |
| `type` | string | `video` (default) `audio` `pptx` `h5p` `image` |

**Per-type limits:** video 1.2 GB, audio 50 MB, pptx 30 MB, h5p 400 MB, image 1 MB. Images restricted to `.png/.jpg/.jpeg`. Videos get `+faststart`; H5P archives are extracted.

**201**
```json
{ "success": true, "url": "media/<file>", "filename": "<file>" }
```

**400/403/413** — no file / invalid path / file too large

---

## 7. `GET` and `PUT /output/<job_dir>/<filename>`

- **GET** serves the rendered HTML and its assets.
- **PUT** saves edited content.

**PUT request**
```json
{ "body_html": "<updated inner HTML of the document body>" }
```
Replaces the content inside `<article class="document-body">…</article>` and rebuilds the TOC.

**200** — `{ "success": true, "message": "Content saved" }`
**400** — non-`.html` / missing `body_html` · **403** — invalid path · **404** — file not found · **500** — write failed / content section not found

---

## 8. `POST /publish`

Publishes the edited document to OCI Object Storage for learners.

**Request**
```json
{ "job_dir": "<url-encoded job dir>", "filename": "<url-encoded>.html" }
```

**200**
```json
{
  "success": true,
  "url": "https://objectstorage.ap-hyderabad-1.oraclecloud.com/n/<ns>/b/poc-interactivetxtbk1/o/…",
  "media_count": 46,
  "video_count": 0
}
```

**400** — missing fields / non-`.html` · **403** — invalid path · **404** — file not found · **500** — publish failed

**Side effect:** sets `render_url` and `status="published"` on the job record.

---

## 9. `POST /export-cms`

Exports content to a CMS. Behavior depends on `platform`.

**Common fields:** `platform` (`strapi` | `wordpress`), `base_url`, `title`, `body_html`, `job_dir`.

**Strapi (DIKSHA):** additionally requires `api_token` (JWT) and `textbook_id`; optional `chapter_order`. Uploads media to the Strapi media library, then creates a chapter with sections.

**200 (strapi)**
```json
{ "success": true, "message": "Created chapter with N/M sections, K media files uploaded", "url": "<admin url>" }
```

**WordPress:** requires `username`, `password`; creates a draft post via `/wp-json/wp/v2/posts`.

**Errors:** `400` unsupported platform / missing token / missing textbook id · `502` cannot connect · `504` timeout.

---

## 10. `GET /api/label-info/<term>`

Fetches a short description for a diagram label from the Wikipedia REST summary API. Always returns `200` with a best-effort payload (falls back gracefully).

**200**
```json
{ "success": true, "title": "Chloroplast", "description": "organelle", "extract": "…", "thumbnail": "https://…" }
```
**400** — `Term too short` (fewer than 2 chars)

---

## 11. `POST /api/make-interactive`

Detects labels on a diagram image and returns their positions (for building clickable diagrams).

**Request**
```json
{ "image_url": "images/<file>.png"  /* local relative, or a full http(s) URL */ }
```
Full URLs are downloaded to a temp file; local paths are resolved under the output dir with traversal guards.

**200** — detector result with `"success": true` · **400** — `image_url required` / detection failed · **404** — image not found · **403** — invalid path

---

## 12. Video Progress

### `POST /api/progress`
```json
{ "learner_id": "…", "video_src": "…", "current_time": 42.5, "duration": 300 }
```
**200** — `{ "success": true, "progress": { "percentage": 14.2, "completed": false, … } }` (completed becomes `true` at ≥ 90%).
**400** — `No data` / `learner_id and video_src required`

### `GET /api/progress/<learner_id>/<video_src>`
**200** — `{ "progress": { … } }` or `{ "progress": null }`

---

## 13. `POST /api/generate-captions/<video_path>`

Generates WebVTT captions with `faster-whisper` (model `tiny`, `int8`), saves a `.vtt` alongside the video, and returns it.

**200**
```json
{ "success": true, "vtt_url": "/output/<path>.vtt", "vtt_content": "WEBVTT\n\n00:00:00.000 --> 00:00:04.000\n…" }
```
**404** — video not found · **500** — caption generation failed

---

## Notes on Status & Error Conventions

- Progress and lookup responses reuse the same job record; `/convert-status` returns internal status, `/api/lookup` returns the mapped external status.
- Path parameters that include slashes or encoded characters (job dirs, filenames, video paths) use Flask `<path:…>` converters and are URL-decoded server-side.
- Traversal protection is applied consistently: `..` rejection and `resolve().relative_to(OUTPUT_DIR)`.
