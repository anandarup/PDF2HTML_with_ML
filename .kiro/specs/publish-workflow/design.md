# Design: Publish for Learners Workflow

## Architecture Overview

The publish workflow adds a new endpoint (`POST /publish`) to the existing Flask app and a corresponding frontend button + modal in `document.html`. It uses AWS S3 (via boto3) to upload HTML, media, and video assets to three separate buckets, then returns the public learner URL.

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser (document.html)                                        │
│                                                                 │
│  [Publish for Learners] ──POST /publish──▶ Flask app.py         │
│                                           │                     │
│  ◀── { url, status } ◀───────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
                                            │
                         ┌──────────────────┼──────────────────┐
                         ▼                  ▼                  ▼
                  ┌─────────────┐  ┌───────────────┐  ┌───────────────┐
                  │ S3 Bucket   │  │ S3 Bucket     │  │ S3 Bucket     │
                  │ poc-inter-  │  │ poc-inter-    │  │ poc-inter-    │
                  │ activetxtbk1│  │ activetxt-    │  │ activetxt-    │
                  │ (HTML)      │  │ media-src-    │  │ media-dst-    │
                  │             │  │ bucket (media)│  │ bucket (video)│
                  └─────────────┘  └───────────────┘  └───────────────┘
```

---

## Component Design

### 1. Backend: `python_app/s3_publish.py` (New File)

Encapsulates all S3 publish logic.

```pseudocode
MODULE s3_publish

IMPORT boto3, os, mimetypes, re, logging

CONSTANTS:
  HTML_BUCKET = "poc-interactivetxtbk1"
  MEDIA_BUCKET = "poc-interactivetxt-media-src-bucket"
  VIDEO_BUCKET = "poc-interactivetxt-media-dst-bucket"
  S3_REGION = configured via env var AWS_REGION or default "ap-south-1"
  VIDEO_EXTENSIONS = {".mp4", ".m3u8", ".ts", ".webm"}

FUNCTION get_s3_client():
  RETURN boto3.client("s3", region_name=S3_REGION)

FUNCTION get_public_url(bucket, key):
  RETURN f"https://{bucket}.s3.{S3_REGION}.amazonaws.com/{key}"

FUNCTION publish_document(job_dir, filename):
  """
  Main publish orchestrator.
  Returns: { "html_url": str, "media_uploaded": int, "videos_uploaded": int }
  """
  output_path = resolve output directory for job_dir
  html_path = output_path / filename

  IF NOT html_path.exists():
    RAISE FileNotFoundError

  s3 = get_s3_client()
  base_key = job_dir  // e.g., "abc123_document"

  // Step 1: Collect and upload media files
  media_map = {}  // local_path → s3_url
  video_map = {}  // local_path → s3_url

  FOR each file in output_path recursively:
    relative = file relative to output_path
    IF file extension IN VIDEO_EXTENSIONS:
      key = f"{base_key}/{relative}"
      upload_to_s3(s3, file, VIDEO_BUCKET, key)
      video_map[relative] = get_public_url(VIDEO_BUCKET, key)
    ELSE IF file is media (image, audio, pdf, h5p, pptx) AND NOT .html:
      key = f"{base_key}/{relative}"
      upload_to_s3(s3, file, MEDIA_BUCKET, key)
      media_map[relative] = get_public_url(MEDIA_BUCKET, key)

  // Step 2: Rewrite HTML paths to S3 URLs
  html_content = read html_path
  FOR relative, url IN media_map + video_map:
    html_content = replace all occurrences of relative path with url

  // Step 3: Upload rewritten HTML
  html_key = f"{base_key}/{filename}"
  upload_bytes_to_s3(s3, html_content.encode(), HTML_BUCKET, html_key, "text/html")
  html_url = get_public_url(HTML_BUCKET, html_key)

  RETURN {
    "html_url": html_url,
    "media_uploaded": len(media_map),
    "videos_uploaded": len(video_map)
  }

FUNCTION upload_to_s3(s3_client, file_path, bucket, key):
  content_type = guess mimetype from file extension
  s3_client.upload_file(
    str(file_path), bucket, key,
    ExtraArgs={"ContentType": content_type}
  )
```

### 2. Backend: Flask Route Addition in `app.py`

```pseudocode
ROUTE POST /publish:
  INPUT JSON: { "job_dir": str, "filename": str }

  VALIDATE job_dir and filename (path traversal protection)
  
  TRY:
    result = s3_publish.publish_document(job_dir, filename)
    RETURN JSON { "success": true, "url": result["html_url"], 
                  "media_count": result["media_uploaded"],
                  "video_count": result["videos_uploaded"] }
  CATCH FileNotFoundError:
    RETURN 404 { "success": false, "error": "Document not found" }
  CATCH boto3 exceptions:
    RETURN 500 { "success": false, "error": error message }
```

### 3. Frontend: Publish Button in `document.html`

**Button placement:** After the existing "Export" toggle button, add a "Publish for Learners" button.

```pseudocode
HTML ELEMENT:
  <button class="edit-toggle publish-btn" id="publishToggle" 
          aria-label="Publish for learners" style="right:21rem;">
    Publish for Learners
  </button>
```

**Visibility:** Shown only when document has been saved (no unsaved changes).

### 4. Frontend: Publish Modal

```pseudocode
MODAL: publishModal
  STATES: idle | publishing | success | error

  ON "idle":
    Show confirmation text: "Publish this document for learners?"
    Show [Publish] and [Cancel] buttons

  ON "publishing":
    Show progress spinner/bar with status text
    Disable all buttons

  ON "success":
    Show "Published successfully!" message
    Show learner URL in a read-only input field
    Show [Copy URL] button (copies to clipboard)
    Show [Close] button

  ON "error":
    Show error message
    Show [Retry] and [Close] buttons
```

### 5. Frontend: Publish JavaScript Logic

```pseudocode
FUNCTION publishForLearners():
  SET modal state = "publishing"
  
  // Extract job_dir and filename from current URL path
  // URL pattern: /output/<job_dir>/<filename>
  parts = window.location.pathname.split("/")
  job_dir = parts[2]  // after /output/
  filename = parts[3]

  TRY:
    response = await fetch("/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_dir, filename })
    })
    data = await response.json()
    
    IF data.success:
      SET modal state = "success"
      Display data.url
    ELSE:
      SET modal state = "error"
      Display data.error
  CATCH:
    SET modal state = "error"
    Display network error message

FUNCTION copyPublishUrl():
  navigator.clipboard.writeText(url)
  Show "Copied!" feedback
```

---

## Data Flow

1. Editor clicks "Publish for Learners"
2. Frontend shows confirmation modal
3. Editor confirms → `POST /publish` with `{ job_dir, filename }`
4. Backend scans output directory for media/video files
5. Backend uploads media → `poc-interactivetxt-media-src-bucket`
6. Backend uploads videos → `poc-interactivetxt-media-dst-bucket`
7. Backend rewrites HTML paths to S3 URLs
8. Backend uploads rewritten HTML → `poc-interactivetxtbk1`
9. Backend returns `{ success: true, url: "..." }`
10. Frontend displays URL with copy button

---

## S3 Object Key Structure

```
poc-interactivetxtbk1/
  └── <job_dir>/
      └── <filename>.html

poc-interactivetxt-media-src-bucket/
  └── <job_dir>/
      ├── images/
      │   ├── figure1.png
      │   └── table2.jpg
      ├── media/
      │   ├── slides.pptx
      │   └── activity.h5p
      └── ...

poc-interactivetxt-media-dst-bucket/
  └── <job_dir>/
      └── media/
          ├── lecture1.mp4
          └── lecture2.mp4
```

---

## Re-publish Behavior

- Same `job_dir` + `filename` → same S3 keys → S3 `put_object` overwrites existing objects
- Learner URL remains identical across publishes
- No versioning needed (latest publish is always what learners see)

---

## Error Handling Strategy

| Error | Behavior |
|-------|----------|
| S3 credentials missing | Return 500 with descriptive message |
| Network timeout during upload | Retry up to 2 times per file, then fail |
| File not found locally | Return 404 |
| Partial upload failure | Report which files failed; HTML not uploaded if media fails |

---

## Dependencies

- **New:** `boto3` (AWS SDK for Python) — add to requirements.txt
- **Existing:** Flask, os, mimetypes, re, logging

---

## Configuration

Environment variables (server-side):
- `AWS_REGION` — S3 region (default: `ap-south-1`)
- `AWS_ACCESS_KEY_ID` — if not using IAM role
- `AWS_SECRET_ACCESS_KEY` — if not using IAM role

No frontend configuration needed — bucket names are server-side constants.
