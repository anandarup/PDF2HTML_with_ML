# Architecture Selection: Inline Notes

## Recommended Architecture: Entity-Oriented with Local-First Offline Cache

### Rationale
Combines the simplicity of Candidate A (PostgreSQL uniqueness enforcement, REST API, low cross-cutting) with Candidate C's offline capability (IndexedDB cache + sync engine). Notes are instant on the client (read/write from IndexedDB), then synced to PostgreSQL when online. The sync engine handles conflicts via last-write-wins with timestamps. Cross-cutting concerns remain low (20% reqs) because the sync engine is a thin bridge, not a full event-sourcing system. Trade-off: slightly more client-side complexity, but the learning platform operates in low-connectivity environments (rural India) where offline support directly impacts usability.

### Components

| Component | Owned State | Responsibility |
|-----------|-------------|----------------|
| **NotesAPI** | None (stateless) | REST endpoints for CRUD; validates input, enforces user isolation, delegates to store |
| **NotesStore** (PostgreSQL) | note records (note_id, user_id, document_id, section_id, note_text, timestamps) | Source of truth; uniqueness constraint; indexed queries |
| **NotesPanelUI** | indicator_visible, note_expanded, active editing state | Renders margin indicators, handles expand/collapse, triggers saves |
| **LocalNotesCache** (IndexedDB) | Full local copy of user's notes + pending changes queue | Zero-latency reads; writes are instant; persists offline |
| **SyncEngine** | sync_status, pending_queue, last_sync_timestamp | Reconciles local cache with server; handles conflicts; retries on reconnect |
| **AuthGateway** | None | Validates JWT, extracts user_id |

### Information Flow

| From \ To | NotesAPI | NotesStore | NotesPanelUI | LocalNotesCache | SyncEngine | AuthGateway |
|-----------|----------|------------|--------------|-----------------|------------|-------------|
| **NotesAPI** | — | → | | | | → |
| **NotesStore** | ← | — | | | | |
| **NotesPanelUI** | | | — | → (read/write) | | |
| **LocalNotesCache** | | | ← (data) | — | ↔ | |
| **SyncEngine** | → (push/pull) | | | ↔ | — | |
| **AuthGateway** | ← | | | | | — |

### Requirement Allocation

| Requirement | Component(s) |
|-------------|--------------|
| FR-1.1 (Create note) | NotesPanelUI → LocalNotesCache → SyncEngine → NotesAPI → NotesStore |
| FR-1.2 (Anchor = heading ID) | NotesPanelUI (reads DOM), LocalNotesCache (stores) |
| FR-1.4 (Visual indicator) | NotesPanelUI |
| FR-2.1 (Fetch all notes) | LocalNotesCache (instant) + SyncEngine (background refresh) |
| FR-2.2 (No layout reflow) | NotesPanelUI |
| FR-2.3 (Expand/collapse) | NotesPanelUI |
| FR-3.1 (Edit note) | NotesPanelUI → LocalNotesCache → SyncEngine |
| FR-3.2 (Auto-save debounce) | NotesPanelUI (debounce) → LocalNotesCache (immediate persist) |
| FR-4.1 (Delete note) | NotesPanelUI → LocalNotesCache → SyncEngine |
| FR-5.1 (Persist across sessions) | LocalNotesCache (IndexedDB) + NotesStore (PostgreSQL) |
| FR-5.2 (Scoped to user+doc+section) | NotesStore (compound key) + LocalNotesCache (same key) |
| FR-5.3 (Upsert behavior) | NotesStore (ON CONFLICT UPDATE) + LocalNotesCache (put) |
| FR-6.1 (Privacy) | NotesAPI (WHERE user_id) + LocalNotesCache (scoped to logged-in user) |
| NFR-1.1 (<200ms) | LocalNotesCache (<5ms from IndexedDB) |
| NFR-2.1 (100K users, 10M notes) | NotesStore (PostgreSQL) |
| NFR-3.1 (Survive re-publish) | NotesStore + LocalNotesCache (section_id is stable) |
| **OFFLINE** (new) | LocalNotesCache + SyncEngine |

### Key Design-Induced Invariants

1. **LocalNotesCache is always the read source** — NotesPanelUI never calls NotesAPI directly for reads
2. **SyncEngine is the only writer to NotesAPI** — NotesPanelUI writes to LocalNotesCache, SyncEngine syncs to server
3. **Last-write-wins conflict resolution** — `updated_at` timestamp determines winner when local and server diverge
4. **Pending queue survives app restart** — stored in IndexedDB alongside notes
5. **NotesStore UNIQUE constraint still enforced** — server rejects duplicates; SyncEngine handles 409 Conflict by merging

### Alternatives Considered

| Candidate | Strength | Weakness | Why Not Selected |
|-----------|----------|----------|-----------------|
| Entity-Oriented (no offline) | Simplest; lowest complexity | No offline; requires connectivity | Platform serves rural India — offline critical |
| Document-Oriented (MongoDB) | Single-doc read; flexible schema | No offline; uniqueness harder | Same connectivity issue |
| Full Event-Sourced | Complete audit trail; undo/redo | 2.5× evolvability cost; over-engineered | Hybrid approach gets offline without full CQRS complexity |

### Metrics Summary

| Metric | Selected (Hybrid) | Entity-Only (A) | Full Event-Sourced (C) |
|--------|-------------------|-----------------|------------------------|
| Cross-cutting reqs % | 20% | 15% | 30% |
| Cross-cutting invariants % | 22% | 17% | 33% |
| Flow density | 8/(6×5) = 0.27 | 4/(4×3) = 0.33 | 7/(5×4) = 0.35 |
| God object score | 35% (LocalNotesCache) | 40% (NotesStore) | 25% |
| Sync cycles | 0 | 0 | 0 |
| Max fan-in | 2 | 2 | 2 |
| Max fan-out | 3 (SyncEngine) | 2 | 3 |
| Evolvability cost | 1.8 | 1.5 | 2.5 |
