# Design: Inline Notes Feature

## Architecture

Entity-Oriented with Local-First Offline Cache. Six components: stateless NotesAPI, PostgreSQL NotesStore, NotesPanelUI (CMS shell), LocalNotesCache (IndexedDB), SyncEngine, and AuthGateway (middleware).

**Key principle:** The UI always reads from and writes to IndexedDB (instant). The SyncEngine handles server reconciliation in the background. Users can take notes offline — they sync when connectivity returns.

---

## 1. Database Schema (PostgreSQL — Server Source of Truth)

### Table: `user_notes`

```sql
CREATE TABLE user_notes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         VARCHAR(64) NOT NULL,
    document_id     VARCHAR(255) NOT NULL,
    section_id      VARCHAR(255) NOT NULL,
    note_text       TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Enforce one note per user per section per document
    CONSTRAINT uq_user_doc_section UNIQUE (user_id, document_id, section_id)
);

-- Primary query pattern: fetch all notes for a user + document
CREATE INDEX idx_user_notes_lookup ON user_notes (user_id, document_id);

-- Sync pattern: fetch notes updated since last sync
CREATE INDEX idx_user_notes_sync ON user_notes (user_id, updated_at);

-- Secondary: "My Notes" view across all documents
CREATE INDEX idx_user_notes_user ON user_notes (user_id, updated_at DESC);
```

### Why PostgreSQL over MongoDB

| Factor | PostgreSQL | MongoDB |
|--------|-----------|---------|
| Uniqueness (INV-1) | `UNIQUE` constraint — enforced at storage level | Compound unique index on nested fields (fragile) |
| Upsert | `INSERT ... ON CONFLICT DO UPDATE` — atomic, single statement | `$set` with upsert works but less robust for conflicts |
| Sync queries | `WHERE updated_at > $last_sync` with index — efficient delta fetch | Same capability |
| Schema evolution | ALTER TABLE (controlled) | Flexible but risks drift |
| Ecosystem fit | Strapi default DB — no new infrastructure | Requires separate deployment |

---

## 2. Client-Side Schema (IndexedDB — Local Cache)

### Object Store: `notes`

```javascript
// IndexedDB schema
const db = await openDB('notes-cache', 1, {
  upgrade(db) {
    const store = db.createObjectStore('notes', { keyPath: 'localKey' });
    // localKey = `${user_id}:${document_id}:${section_id}`
    store.createIndex('by_document', ['user_id', 'document_id']);
    store.createIndex('by_sync_status', 'syncStatus');
  }
});
```

### Note Record (Local)

```typescript
interface LocalNote {
  localKey: string;          // composite: `${userId}:${docId}:${sectionId}`
  id?: string;              // server UUID (null for unsynced creates)
  user_id: string;
  document_id: string;
  section_id: string;
  note_text: string;
  created_at: string;
  updated_at: string;       // client timestamp (used for conflict resolution)
  syncStatus: 'synced' | 'pending_create' | 'pending_update' | 'pending_delete';
}
```

---

## 3. API Design

### Base URL
```
/api/notes
```

### Authentication
All endpoints require `Authorization: Bearer <token>`. The `user_id` is extracted server-side.

---

### GET /api/notes?document_id={docId}&since={timestamp}

Fetch notes for the authenticated user. Supports delta sync via `since` parameter.

**Query Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| document_id | string | Yes | Document identifier |
| since | ISO timestamp | No | Only return notes updated after this time (for delta sync) |

**Response (200):**
```json
{
  "notes": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "section_id": "5-2-nutrition",
      "note_text": "Remember: autotrophs make their own food",
      "created_at": "2026-08-27T10:30:00Z",
      "updated_at": "2026-08-27T14:15:00Z",
      "deleted": false
    }
  ],
  "server_time": "2026-08-28T09:00:00Z"
}
```

**Note:** When `since` is provided, deleted notes are returned with `"deleted": true` so the client can remove them from cache. `server_time` is used as the next `since` value.

---

### PUT /api/notes

Create or update a note (upsert).

**Request Body:**
```json
{
  "document_id": "a88b7d1e_Chap 5/a88b7d1e_Chap 5.html",
  "section_id": "5-2-nutrition",
  "note_text": "Remember: autotrophs make their own food",
  "client_updated_at": "2026-08-28T09:00:00Z"
}
```

**Response (200):**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "section_id": "5-2-nutrition",
  "note_text": "Remember: autotrophs make their own food",
  "created_at": "2026-08-27T10:30:00Z",
  "updated_at": "2026-08-28T09:00:00Z"
}
```

**Response (409 — Conflict):**
```json
{
  "conflict": true,
  "server_note": { "id": "...", "note_text": "...", "updated_at": "..." },
  "resolution": "server_wins"
}
```
Client handles by accepting server version or re-submitting with force flag.

---

### DELETE /api/notes/:noteId

**Response (204):** Success.
**Response (404):** Not found or not owned by user.

---

### POST /api/notes/sync

Batch sync endpoint — pushes multiple pending changes in one request.

**Request Body:**
```json
{
  "changes": [
    { "action": "upsert", "document_id": "...", "section_id": "...", "note_text": "...", "client_updated_at": "..." },
    { "action": "delete", "id": "550e8400-..." }
  ],
  "last_sync": "2026-08-27T00:00:00Z"
}
```

**Response (200):**
```json
{
  "results": [
    { "action": "upsert", "section_id": "...", "status": "ok", "id": "...", "updated_at": "..." },
    { "action": "delete", "id": "...", "status": "ok" }
  ],
  "server_updates": [
    { "id": "...", "section_id": "...", "note_text": "...", "updated_at": "...", "deleted": false }
  ],
  "server_time": "2026-08-28T09:00:05Z"
}
```

This single endpoint handles both push (local→server) and pull (server→local) in one round-trip, minimizing network calls.

---

## 4. SyncEngine Implementation

```javascript
class SyncEngine {
  constructor(db, apiBase, authToken) {
    this.db = db;        // IndexedDB instance
    this.apiBase = apiBase;
    this.authToken = authToken;
    this.lastSync = localStorage.getItem('notes_last_sync') || null;
    this.syncing = false;
  }

  async sync() {
    if (this.syncing) return;
    this.syncing = true;

    try {
      // 1. Gather pending local changes
      const tx = this.db.transaction('notes', 'readonly');
      const pendingIndex = tx.store.index('by_sync_status');
      const pending = [];
      for await (const cursor of pendingIndex.iterate(IDBKeyRange.bound('pending_create', 'pending_update'))) {
        pending.push(this.toSyncPayload(cursor.value));
      }
      // Also gather pending deletes
      for await (const cursor of pendingIndex.iterate('pending_delete')) {
        pending.push({ action: 'delete', id: cursor.value.id });
      }

      // 2. Send batch sync request
      const res = await fetch(`${this.apiBase}/notes/sync`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.authToken}`
        },
        body: JSON.stringify({
          changes: pending,
          last_sync: this.lastSync
        })
      });

      if (!res.ok) throw new Error(`Sync failed: ${res.status}`);
      const data = await res.json();

      // 3. Apply results — mark local notes as synced
      const writeTx = this.db.transaction('notes', 'readwrite');
      for (const result of data.results) {
        if (result.status === 'ok') {
          if (result.action === 'delete') {
            await writeTx.store.delete(result.localKey);
          } else {
            const existing = await writeTx.store.get(result.localKey);
            if (existing) {
              existing.id = result.id;
              existing.updated_at = result.updated_at;
              existing.syncStatus = 'synced';
              await writeTx.store.put(existing);
            }
          }
        }
      }

      // 4. Apply server updates (other devices' changes)
      for (const update of data.server_updates) {
        const localKey = `${this.userId}:${update.document_id}:${update.section_id}`;
        if (update.deleted) {
          await writeTx.store.delete(localKey);
        } else {
          const existing = await writeTx.store.get(localKey);
          // Only overwrite if server is newer (last-write-wins)
          if (!existing || new Date(update.updated_at) > new Date(existing.updated_at)) {
            await writeTx.store.put({
              localKey,
              id: update.id,
              user_id: this.userId,
              document_id: update.document_id,
              section_id: update.section_id,
              note_text: update.note_text,
              created_at: update.created_at,
              updated_at: update.updated_at,
              syncStatus: 'synced'
            });
          }
        }
      }

      await writeTx.done;

      // 5. Update last sync timestamp
      this.lastSync = data.server_time;
      localStorage.setItem('notes_last_sync', this.lastSync);

    } catch (err) {
      console.warn('Notes sync failed (will retry):', err.message);
    } finally {
      this.syncing = false;
    }
  }

  // Auto-sync: on online event, on visibility change, and periodically
  startAutoSync() {
    // Sync when coming back online
    window.addEventListener('online', () => this.sync());

    // Sync when tab becomes visible (user returns)
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) this.sync();
    });

    // Periodic sync every 30s when online
    setInterval(() => {
      if (navigator.onLine) this.sync();
    }, 30000);

    // Initial sync
    if (navigator.onLine) this.sync();
  }

  toSyncPayload(note) {
    return {
      action: 'upsert',
      document_id: note.document_id,
      section_id: note.section_id,
      note_text: note.note_text,
      client_updated_at: note.updated_at
    };
  }
}
```

---

## 5. Frontend Component (NotesPanelUI)

### 5.1 Reading Notes (Always from IndexedDB)

```javascript
class NotesPanel {
  constructor(iframeEl, documentId, userId) {
    this.iframe = iframeEl;
    this.documentId = documentId;
    this.userId = userId;
    this.notes = new Map();
  }

  async loadNotes() {
    // Always read from local cache (instant, works offline)
    const tx = this.db.transaction('notes', 'readonly');
    const index = tx.store.index('by_document');
    const results = await index.getAll([this.userId, this.documentId]);
    results.forEach(n => {
      if (n.syncStatus !== 'pending_delete') {
        this.notes.set(n.section_id, n);
      }
    });
    this.renderIndicators();
  }

  async createOrUpdateNote(sectionId, text) {
    const localKey = `${this.userId}:${this.documentId}:${sectionId}`;
    const now = new Date().toISOString();
    
    const note = {
      localKey,
      user_id: this.userId,
      document_id: this.documentId,
      section_id: sectionId,
      note_text: text,
      updated_at: now,
      created_at: now,
      syncStatus: 'pending_create' // or 'pending_update' if exists
    };

    // Check if exists
    const existing = await this.db.get('notes', localKey);
    if (existing) {
      note.id = existing.id;
      note.created_at = existing.created_at;
      note.syncStatus = 'pending_update';
    }

    // Write to IndexedDB (instant, works offline)
    await this.db.put('notes', note);
    this.notes.set(sectionId, note);
    this.renderIndicators();

    // Trigger sync if online
    if (navigator.onLine) {
      this.syncEngine.sync();
    }
  }

  async deleteNote(sectionId) {
    const localKey = `${this.userId}:${this.documentId}:${sectionId}`;
    const existing = await this.db.get('notes', localKey);
    
    if (existing && existing.id) {
      // Mark for server deletion on next sync
      existing.syncStatus = 'pending_delete';
      await this.db.put('notes', existing);
    } else {
      // Never synced — just remove locally
      await this.db.delete('notes', localKey);
    }

    this.notes.delete(sectionId);
    this.renderIndicators();

    if (navigator.onLine) {
      this.syncEngine.sync();
    }
  }
}
```

### 5.2 Offline Indicator

```javascript
// Show sync status to user
function renderSyncStatus() {
  const indicator = document.getElementById('sync-status');
  if (!navigator.onLine) {
    indicator.textContent = '📴 Offline — notes saved locally';
    indicator.className = 'sync-offline';
  } else if (syncEngine.syncing) {
    indicator.textContent = '🔄 Syncing...';
    indicator.className = 'sync-active';
  } else {
    indicator.textContent = '✓ Synced';
    indicator.className = 'sync-done';
  }
}

window.addEventListener('online', renderSyncStatus);
window.addEventListener('offline', renderSyncStatus);
```

### 5.3 Margin Indicator Rendering (Same as before — zero reflow)

Uses absolute positioning and postMessage from iframe for section positions. No change from previous design.

### 5.4 Auto-Save with Debounce

```javascript
let debounceTimer = null;

function onNoteTextChange(sectionId, newText) {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    notesPanel.createOrUpdateNote(sectionId, newText);
    // Writes to IndexedDB immediately → syncs to server when possible
  }, 1000); // 1s debounce (shorter than before since writes are local)
}
```

---

## 6. Conflict Resolution Strategy

**Policy: Last-Write-Wins (by timestamp)**

| Scenario | Resolution |
|----------|-----------|
| User edits offline, comes back online, server has same note unchanged | Client wins (push local version) |
| User edits offline, someone edits on another device (server newer) | Server wins (newer timestamp) |
| User edits offline, same user edits on another device same section | Last timestamp wins |
| User deletes offline, server has update | Delete wins (explicit user action) |

The `POST /api/notes/sync` endpoint returns conflicts explicitly so the client can show a toast: "Note updated on another device — showing latest version."

---

## 7. Payload Optimization for High Traffic

| Strategy | Implementation |
|----------|---------------|
| Delta sync | `since` parameter — only fetch changes since last sync |
| Batch sync | Single `POST /api/notes/sync` pushes + pulls in one request |
| Local-first reads | UI never waits for network (IndexedDB is <5ms) |
| Debounced writes | 1s debounce before persisting to IndexedDB |
| Background sync | 30s periodic + on-online + on-visibility-change |
| Minimal payload | Only note_text + section_id + timestamps (no echoing user_id or document_id) |

---

## 8. Implementation on poc-interactivetxtbk Side

Add `reportSectionPositions()` to the `LEARNER_RUNTIME_SCRIPT` in `s3_publish.py`:

```javascript
// Report heading positions to parent frame for notes margin indicators
function reportSectionPositions() {
  var headings = document.querySelectorAll('h1[id], h2[id], h3[id]');
  var positions = [];
  headings.forEach(function(h) {
    var rect = h.getBoundingClientRect();
    positions.push({ id: h.id, top: rect.top + window.scrollY });
  });
  window.parent.postMessage({ type: 'sectionPositions', positions: positions }, '*');
}
reportSectionPositions();
var _spTimer;
window.addEventListener('scroll', function() {
  clearTimeout(_spTimer);
  _spTimer = setTimeout(reportSectionPositions, 200);
}, { passive: true });
```

Everything else (API, database, sync, UI) lives on the Strapi/CMS side.
