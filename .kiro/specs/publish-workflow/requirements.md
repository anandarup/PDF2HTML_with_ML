# Requirements: Publish for Learners Workflow

## Overview

Editors use PDF2WebView to import PDFs, convert them to interactive HTML, and edit the content. Once editing is finalized, they click "Publish for Learners" to distribute the content to learners via S3 buckets. After publishing, the editor receives a shareable URL. Editors can re-edit and re-publish the same document at any time.

---

## Functional Requirements

### FR-1: Publish for Learners Button

- **FR-1.1:** A "Publish for Learners" button SHALL be displayed in the editor toolbar (or as a prominent action button) when the document is in edit mode or after saving.
- **FR-1.2:** The button SHALL be clearly distinguishable from the existing "Save" and "Export" buttons.
- **FR-1.3:** The button SHALL be disabled while unsaved changes exist (editor must save first).

### FR-2: Publish Action — HTML to S3

- **FR-2.1:** Clicking "Publish for Learners" SHALL upload the finalized HTML document to the S3 bucket `poc-interactivetxtbk1`.
- **FR-2.2:** The HTML SHALL be cleaned of editor UI chrome (block controls, contenteditable attributes, editing-only elements) before upload.
- **FR-2.3:** The uploaded HTML object key SHALL follow a predictable naming convention (e.g., `<job_id>/<filename>.html`) so the same document overwrites on re-publish.

### FR-3: Publish Action — Media Files to S3

- **FR-3.1:** All related media files (images, PDFs, audio, H5P, pptx) SHALL be uploaded to `poc-interactivetxt-media-src-bucket`.
- **FR-3.2:** All streaming video files (.mp4, HLS segments) SHALL be uploaded to `poc-interactivetxt-media-dst-bucket`.
- **FR-3.3:** Media paths within the published HTML SHALL be rewritten to point to the corresponding S3 bucket public URLs.
- **FR-3.4:** Only new or modified media files SHALL be uploaded (avoid redundant uploads on re-publish if possible).

### FR-4: Post-Publish URL Generation

- **FR-4.1:** After successful publish, the system SHALL display a shareable learner URL to the editor.
- **FR-4.2:** The URL SHALL directly serve the published HTML document from `poc-interactivetxtbk1`.
- **FR-4.3:** The URL SHALL be presented in a modal/dialog with a "Copy to Clipboard" action.
- **FR-4.4:** The URL format SHALL be: `https://<bucket-domain>/<namespace>/b/poc-interactivetxtbk1/o/<object_path>`

### FR-5: Re-edit and Re-publish

- **FR-5.1:** After publishing, the editor SHALL still be able to enter edit mode on the same document.
- **FR-5.2:** Re-publishing SHALL overwrite the previously published content at the same URL (same S3 object key).
- **FR-5.3:** The learner URL SHALL remain stable across re-publishes (no URL change).

### FR-6: Publish Status Feedback

- **FR-6.1:** The system SHALL display a progress indicator during the publish operation (uploading HTML, media, videos).
- **FR-6.2:** On success, the system SHALL display a success message with the learner URL.
- **FR-6.3:** On failure, the system SHALL display a meaningful error message indicating which step failed.

---

## Non-Functional Requirements

### NFR-1: Performance

- **NFR-1.1:** Publish operation SHOULD complete within 60 seconds for documents with up to 50 media files.
- **NFR-1.2:** Large video files SHOULD show individual upload progress.

### NFR-2: Security

- **NFR-2.1:** S3 bucket credentials SHALL be managed server-side (never exposed to frontend).
- **NFR-2.2:** Only authenticated editors SHALL be able to trigger publish (rely on existing session/auth if present).

### NFR-3: Reliability

- **NFR-3.1:** Failed uploads SHALL NOT leave partially published content (rollback or atomic publish).
- **NFR-3.2:** Network interruption during publish SHALL present a retry option.

### NFR-4: Compatibility

- **NFR-4.1:** The publish workflow SHALL work alongside the existing OCI storage integration (conversion-time upload) and CMS Export.
- **NFR-4.2:** Published learner URLs SHALL work in modern browsers without requiring authentication.

---

## User Stories

| ID | Story | Acceptance Criteria |
|----|-------|-------------------|
| US-1 | As an editor, I want to publish my finalized document so learners can access it via a URL. | Clicking "Publish for Learners" uploads all assets and returns a working URL. |
| US-2 | As an editor, I want to copy the learner URL easily so I can share it via email/LMS. | A copy-to-clipboard button is provided in the success dialog. |
| US-3 | As an editor, I want to re-edit and re-publish without changing the learner URL. | Re-publishing overwrites the same S3 objects; the URL remains unchanged. |
| US-4 | As an editor, I want to see publish progress so I know the operation is working. | A progress bar/status text updates during upload. |

---

## Assumptions

1. The project will use AWS S3 (boto3) for the three target buckets, not OCI Object Storage.
2. The S3 buckets (`poc-interactivetxtbk1`, `poc-interactivetxt-media-src-bucket`, `poc-interactivetxt-media-dst-bucket`) already exist and have appropriate public read policies for learner access.
3. AWS credentials are available on the server via IAM role, environment variables, or AWS config.
4. Video files considered "streaming" are `.mp4` files (possibly with `+faststart` optimization already applied during upload-media).
5. The existing "Export" button for CMS export remains unchanged and is separate from this workflow.
