# Tasks: Publish for Learners Workflow

## Task 1: Create `s3_publish.py` module

**File:** `python_app/s3_publish.py`

Create a new module that handles all S3 publishing logic:

- [ ] 1.1: Define constants: `HTML_BUCKET`, `MEDIA_BUCKET`, `VIDEO_BUCKET`, `S3_REGION`, `VIDEO_EXTENSIONS`
- [ ] 1.2: Implement `get_s3_client()` — returns boto3 S3 client using region from env var or default
- [ ] 1.3: Implement `get_public_url(bucket, key)` — constructs the public S3 URL
- [ ] 1.4: Implement `upload_to_s3(s3_client, file_path, bucket, key)` — uploads a single file with correct content-type
- [ ] 1.5: Implement `publish_document(job_dir, filename)` — main orchestrator that:
  - Resolves the output directory path
  - Scans for media files and video files
  - Uploads media to `poc-interactivetxt-media-src-bucket`
  - Uploads videos to `poc-interactivetxt-media-dst-bucket`
  - Rewrites HTML local paths to S3 public URLs
  - Uploads final HTML to `poc-interactivetxtbk1`
  - Returns `{ html_url, media_uploaded, videos_uploaded }`

---

## Task 2: Add `/publish` route to `app.py`

**File:** `python_app/app.py`

- [ ] 2.1: Import `s3_publish` module at top of file
- [ ] 2.2: Add `POST /publish` route that:
  - Accepts JSON body `{ "job_dir": str, "filename": str }`
  - Validates inputs (path traversal prevention, file must be .html)
  - Calls `s3_publish.publish_document(job_dir, filename)`
  - Returns JSON response with `success`, `url`, `media_count`, `video_count`
  - Handles errors: FileNotFoundError → 404, boto3 exceptions → 500

---

## Task 3: Add `boto3` dependency

**File:** `python_app/requirements.txt`

- [ ] 3.1: Add `boto3>=1.28.0` to requirements.txt

---

## Task 4: Add "Publish for Learners" button to document template

**File:** `python_app/templates/document.html`

- [ ] 4.1: Add a "Publish for Learners" button element after the existing Export button
- [ ] 4.2: Style the button (green/teal color to distinguish from Edit/Export)
- [ ] 4.3: Button should be visible regardless of edit mode (publish can happen after saving)

---

## Task 5: Add Publish modal to document template

**File:** `python_app/templates/document.html`

- [ ] 5.1: Add a publish confirmation/progress modal (HTML markup) with states: idle, publishing, success, error
- [ ] 5.2: Style the modal consistent with existing modals (formula modal, export modal)
- [ ] 5.3: Success state shows: success message, read-only URL input, Copy URL button, Close button
- [ ] 5.4: Error state shows: error message, Retry button, Close button
- [ ] 5.5: Publishing state shows: spinner/progress text

---

## Task 6: Implement frontend publish JavaScript

**File:** `python_app/templates/document.html`

- [ ] 6.1: Add click handler for the Publish button to show the modal
- [ ] 6.2: Implement `publishForLearners()` function:
  - Extract `job_dir` and `filename` from `window.location.pathname`
  - Send `POST /publish` with JSON body
  - Handle success: display URL, enable copy button
  - Handle error: display error message, enable retry
- [ ] 6.3: Implement `copyPublishUrl()` — copies URL to clipboard with visual feedback
- [ ] 6.4: Implement modal open/close/state transitions
- [ ] 6.5: Disable Publish button if there are unsaved changes (integrate with existing dirty state tracking)

---

## Task 7: Verify end-to-end workflow

- [ ] 7.1: Verify Flask app starts without import errors
- [ ] 7.2: Verify the publish button renders correctly in the document page
- [ ] 7.3: Verify the modal opens/closes and transitions between states
- [ ] 7.4: Verify the `/publish` endpoint responds correctly (with mock or real S3 credentials)

---

## Implementation Order

1. Task 3 (dependency) → Task 1 (backend module) → Task 2 (route) → Task 4 (button) → Task 5 (modal) → Task 6 (JS) → Task 7 (verify)
