# Requirements: Inline Notes Feature

## Overview

Enable learners to attach personal notes to specific sections of a published HTML document. Notes persist across sessions, reappear precisely where they were anchored, and are private to each user.

---

## Functional Requirements

### FR-1: Note Creation
- **FR-1.1:** A learner SHALL be able to create a note anchored to a specific section (heading) of the document.
- **FR-1.2:** The anchor point SHALL be stored as the heading's `id` attribute (stable across re-publishes as long as heading text doesn't change).
- **FR-1.3:** The note text SHALL support plain text (minimum) with optional markdown-like formatting.
- **FR-1.4:** A visual indicator (margin icon) SHALL appear next to anchored sections.

### FR-2: Note Retrieval
- **FR-2.1:** When a document loads, all notes for the current user + document SHALL be fetched in a single API call.
- **FR-2.2:** Notes SHALL be rendered as margin indicators at their anchor positions without causing layout reflows.
- **FR-2.3:** Clicking a margin indicator SHALL expand/collapse the note content inline.

### FR-3: Note Editing
- **FR-3.1:** A learner SHALL be able to edit an existing note's text.
- **FR-3.2:** Edits SHALL auto-save after a debounce period (no explicit "Save" button required).

### FR-4: Note Deletion
- **FR-4.1:** A learner SHALL be able to delete a note.
- **FR-4.2:** Deletion SHALL remove the visual indicator immediately.

### FR-5: Note Persistence
- **FR-5.1:** Notes SHALL persist across browser sessions.
- **FR-5.2:** Notes SHALL be scoped to (user_id, document_id, section_id).
- **FR-5.3:** A user SHALL have at most one note per section (upsert behavior).

### FR-6: Privacy
- **FR-6.1:** Notes are private — a user can only see their own notes.
- **FR-6.2:** The API SHALL enforce user isolation at the query level.

---

## Non-Functional Requirements

### NFR-1: Performance
- **NFR-1.1:** Fetching notes for a document SHALL complete in <200ms for up to 100 notes.
- **NFR-1.2:** Rendering indicators SHALL not cause visible layout shift (CLS = 0).

### NFR-2: Scalability
- **NFR-2.1:** The system SHALL support 100K+ concurrent users with 10M+ total notes.
- **NFR-2.2:** API payloads SHALL be optimized (only note text + anchor, no full document re-fetch).

### NFR-3: Reliability
- **NFR-3.1:** Notes SHALL survive document re-publishing (anchors based on heading IDs, not DOM position).
- **NFR-3.2:** Orphaned notes (anchor no longer exists) SHALL still be accessible via a "My Notes" view.

---

## Constraints

- The published HTML is served from OCI Object Storage (static file) — no server-side rendering.
- The notes system must work via a separate API (not baked into the HTML).
- The CMS (Strapi) shell wraps the published HTML in an iframe — notes UI lives in the parent shell, not inside the iframe.
- Authentication is handled by the CMS (JWT/session) — the notes API must accept the same auth token.
