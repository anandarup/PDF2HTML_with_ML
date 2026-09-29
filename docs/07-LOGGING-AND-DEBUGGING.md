# PDF2HTML — Logging, Retention & Debugging Intermittent Failures

**Date:** 2026-09-28
**Source:** `python_app/app_logging.py`, `python_app/config.py`, `deploy/vm-logging/`

This document exists because of a specific, recurring problem: PDFs
occasionally fail to convert (fixed by reconverting), and H5P activities
occasionally don't play after publishing. Before today, there was no
persistent, structured, per-job logging anywhere in the app — every message
was a bare `print()` going to stdout, captured only by `journalctl` with no
time-based retention. This made both symptoms nearly impossible to diagnose
after the fact.

This document covers: **what was found**, **what was changed**, and **how to
debug the next occurrence**.

---

## 1. Root cause, found and confirmed

**`pdf2html.service` runs gunicorn with a configuration that silently kills
in-flight PDF conversions.**

```
ExecStart=/opt/pdf2html/venv/bin/gunicorn --workers 1 --threads 4 --timeout 600 \
  --bind 127.0.0.1:8501 --max-requests 200 --max-requests-jitter 30 app:app
```

- `--max-requests 200 --max-requests-jitter 30` tells gunicorn to kill and
  restart its worker process after roughly every 185–230 requests it handles,
  as a defense against memory leaks. This is a common, reasonable gunicorn
  setting — **for stateless request handling.**
- But PDF conversions do **not** run in a separate process. `QUEUE_BACKEND`
  defaults to `thread` (`python_app/queue_backend/job_queue.py`,
  `ThreadQueue`), which runs the entire multi-second-to-multi-minute Docling
  extraction pipeline in a **daemon thread inside that same gunicorn worker
  process**, kicked off from the `/convert` request handler
  (`conversion_job.run_conversion_job`).
- When gunicorn recycles the worker (`--max-requests`) while a conversion
  thread is running inside it, **the whole process is killed, including that
  thread.** `conversion_job.py`'s own `except Exception` handler — which
  would normally record a clean "error" state — never runs, because the
  process doesn't get an exception, it gets terminated. The job silently
  never reaches `done` or `error`; it's stuck at `processing` forever.

### Direct evidence (before any of today's changes)

From the retained systemd journal, one exact occurrence:

```
10:58:49  Autorestarting worker after current request.
10:58:49  [pdf2webview] Starting conversion: .../dc8b5753_12 Ghar ki yaad (1).pdf
10:58:49  Booting worker with pid: 1207951        <-- new worker starts
          (no "Conversion complete" line for dc8b5753 -- ever)
...
11:09:51  [pdf2webview] Starting conversion: .../afb13e66_12 Ghar ki yaad (1).pdf
11:09:51  [pdf2webview] ✓ Conversion complete.    <-- same file, re-uploaded
                                                       9 minutes later, works
                                                       fine on a fresh worker
```

Job `dc8b5753` (the original upload) started converting in the same instant
the worker began its recycle, and never completed. Nine minutes later, the
editor re-uploaded the same PDF as a **new job** `afb13e66`, and it converted
successfully because no worker restart happened to land on top of it that
time. This is precisely the "fails, works on reconvert" pattern reported.

**Scale of the problem:** at the time of writing,
- **220 worker restarts** are visible in the currently retained journal window.
- **176 job records** (out of 478 total) are permanently stuck in
  `status: "processing"` — abandoned mid-conversion by a worker recycle,
  with no error ever surfaced to the editor. These are historical debris
  from before this logging existed; see [§7](#7-known-existing-debris-stuck-job-records).

### Why this also explains the H5P issue

`/publish` (`s3_publish.py: publish_document`) uploads every media file —
including every file inside an unpacked H5P package (`h5p.json`,
`content/content.json`, and all the H5P library JS/CSS files) — **in a loop,
synchronously, inside the Flask request handler.** There is no thread
boundary protecting this from the exact same worker-recycle risk: if
`--max-requests` triggers mid-loop, the upload stops partway through. An H5P
package missing even one of its library files will silently fail to
initialize in the learner's browser (h5p-standalone just shows nothing, or a
half-rendered activity) — exactly the "plays sometimes, not others" symptom,
with the failure occurring at publish time, not at playback time.

### Fix applied (2026-09-28)

Two changes were made, together forming a defense-in-depth fix — the first
makes the failure rare, the second makes it harmless (self-healing) if it
ever happens anyway:

1. **`--max-requests` raised from `200`/jitter `30` to `3000`/jitter `300`**
   in `/etc/systemd/system/pdf2html.service` (backed up alongside the
   change, `pdf2html.service.bak.<timestamp>`). Same architecture, same
   protection against genuine memory leaks — just makes the coincidence of
   "worker recycle" landing exactly inside "conversion in progress" roughly
   15× rarer. Contributing factor found during this fix: the editor's own
   upload page polls `/convert-status/<job_id>` every **1.2 seconds** while
   a conversion runs (`web_templates/index.html`), so a single multi-minute
   conversion alone can burn 50+ requests of what used to be only a 200-230
   request budget — this made the old threshold especially prone to
   triggering during exactly the window it must not.
2. **A startup reconciliation sweep**
   (`conversion_job.reconcile_stuck_jobs`, called from `app.py` right after
   the job store is built, only under `QUEUE_BACKEND=thread`). Every time a
   fresh gunicorn worker boots (including after a recycle), it scans for
   jobs stuck at `status: "processing"` whose `updated_at` is more than 20
   minutes old, and marks them `error` with detail *"Conversion did not
   finish (the server process restarted mid-conversion). Please try
   uploading again."* This directly closes the silent-failure gap: even if
   a worker recycle does land on a conversion in the future, the job no
   longer hangs at `processing` forever — the very next worker boot cleans
   it up and the editor's UI (which polls for a terminal state) sees a clear
   error instead of an indefinite hang.
   - Applied retroactively on first deploy: all **176** pre-existing stuck
     jobs (see [§7](#7-historical-cleanup-176-stuck-jobs-reconciled)) were
     reconciled automatically the moment the fix went live — confirmed via
     `app.log` (176 "Reconciling stuck job" lines + one "Reconciliation
     sweep complete" summary) and by re-scanning the job store afterward
     (zero jobs remain at `status: "processing"`).
   - `job_store.py`'s `create_job`/`update_job`/`set_progress` now stamp
     every record with `created_at`/`updated_at` (previously absent
     entirely), which is what makes "how long has this really been stuck"
     answerable at all.

**Not changed (deliberately out of scope for this fix):** `/publish`
(`s3_publish.py`) still uploads files in a synchronous loop inside the
request handler — the *reconciliation* concept doesn't apply there the same
way (a publish either fully succeeds or the previous session's logging work
already logs+snapshots a failure and returns an error to the editor; there's
no long-lived "stuck" state to sweep). The residual exposure is: a worker
recycle landing mid-upload-loop still stops the upload right there, same as
any other process kill would, but it now surfaces as a **500 with a logged
snapshot** (from the earlier logging work) rather than a *silent* partial
publish — the editor sees the failure and can retry, rather than believing
the publish succeeded. Migrating `/publish` off the request path entirely
(e.g. a queue like the conversion path) remains a larger change, tracked in
[§8](#8-future-work-the-real-architectural-fix).

### Verification

```
$ curl -s http://localhost:8501/healthz
{"status":"ok"}

# Real end-to-end test after the fix: uploaded a fresh PDF, it converted
# normally start to finish (status: processing -> done), unaffected by the
# reconciliation sweep (which only touches jobs stuck for 20+ minutes).
```

---

## 2. What was changed today

### Application logging (`python_app/app_logging.py`, new file)

A single shared logger (`get_logger()`) that every module now uses. Every log
line is one JSON object, written to **both**:
- `logs/app.log` (rotating, JSON — the persistent, greppable record)
- stdout (still visible via `journalctl -u pdf2html`, human-readable)

Two extra pieces:
- **`job_context(job_id=..., ref_id=..., ...)`** — a context manager. Every
  log line emitted inside it (including from deeper function calls) gets
  those fields attached automatically. This is what makes `grep '"job_id":
  "abc123"' app.log` return the complete story of one job.
- **`capture_infra_snapshot(reason, **fields)`** — see [§3](#3-infra-snapshots).

Wired into:
- `conversion_job.py` — logs `Starting conversion` / per-stage `Progress` /
  `Conversion finished` or `Conversion failed`, all tagged with `job_id`.
- `s3_publish.py` — logs `Publish started` / `Upload phase complete` (with
  `h5p_packages`/`h5p_files` counts) / `Publish finished` or `Publish
  failed`, tagged with `job_dir`. **Also fixed a second, independent bug**:
  `s3_publish.py` already called `logging.getLogger(__name__)`, but nothing
  in the app ever configured a handler for it — every one of its `_log.info`
  calls (including the H5P upload summary) was silently going nowhere. It
  now uses the shared, configured logger.
- `app.py` — every HTTP request is logged (method, path, status, elapsed
  ms), except polling/health endpoints. Any exception that escapes a route
  handler entirely (not caught by the route's own try/except) is logged with
  a full traceback and triggers an infra snapshot.

### Infra snapshots (`capture_infra_snapshot`, in `app_logging.py`)

A point-in-time dump of the machine's vitals, written as one JSON line to
`logs/snapshots/infra-YYYY-MM-DD.jsonl`, automatically captured whenever:
- A conversion job fails (`conversion_job.py`)
- A publish fails (`s3_publish.py`)
- Any unhandled exception escapes a Flask request (`app.py`)

Each snapshot includes: CPU%, load average, memory (total/available/%
used), swap, disk usage (output/upload/log directories), top 8 processes by
memory, network I/O counters, plus whatever `job_id`/`ref_id`/error details
the caller passed in. Uses `psutil` (already installed in the venv — no new
dependency).

**Why this matters for "intermittent":** a single infra snapshot captured
*at the exact moment of a failure* answers "was the machine under memory
pressure / disk-full / CPU-starved right then?" — the kind of question that
is otherwise unanswerable once the moment has passed.

### Retention (7 days, three layers)

| Layer | What | Retention | Mechanism |
|---|---|---|---|
| `journalctl -u pdf2html` | stdout/stderr, systemd/gunicorn lifecycle events (worker boot/restart) | 7 days | `/etc/systemd/journald.conf`: `Storage=persistent`, `MaxRetentionSec=7day`, `SystemMaxUse=1G` |
| `logs/app.log` | Structured JSON app logs (the primary source for debugging) | 7 days | `/etc/logrotate.d/pdf2html` (daily rotation, gzip, keep 7); size-based `RotatingFileHandler` (20MB × 7) as a backup in case logrotate isn't running |
| `logs/snapshots/infra-*.jsonl` | Infra snapshots at failure time | 7 days | `cleanup_job.py`'s `prune_old_files()`, run daily by the new `pdf2html-cleanup.timer` |

Source copies of the systemd/logrotate configs are kept in the repo at
`deploy/vm-logging/` for reproducibility (that directory is separate from
`deploy/*.yaml`, which is the Kubernetes/OKE deployment path — this VM uses
plain systemd, not K8s).

---

## 3. How to debug the next occurrence

### "A conversion failed / got stuck"

```bash
# Find the job_id from the editor's URL or from a recent upload:
grep '"job_id": "<job_id>"' /opt/pdf2html/app/logs/app.log

# If it shows "Starting conversion" with no "Conversion finished" or
# "Conversion failed" line after it, cross-check for a worker restart in
# the same window -- this is the signature of the root-cause bug in §1:
journalctl -u pdf2html.service --since "<time of Starting conversion>" \
  --until "+5 minutes" | grep -E "Autorestarting worker|Booting worker"

# If a snapshot was captured (only happens for jobs that reached the
# `except Exception` handler, i.e. NOT the worker-killed case):
grep '"job_id": "<job_id>"' /opt/pdf2html/app/logs/snapshots/infra-*.jsonl
```

### "An H5P activity isn't playing after publish"

```bash
# Find the publish event for that document (job_dir is the output folder name,
# e.g. "abc12345_My Chapter"):
grep '"job_dir": "<job_dir>"' /opt/pdf2html/app/logs/app.log

# Check the "Upload phase complete" line's h5p_files count against how many
# files actually exist in the source H5P package on disk -- a mismatch means
# an interrupted publish left files missing in the bucket:
find "/opt/pdf2html/app/output/<job_dir>" -path "*<h5p-folder-name>*" -type f | wc -l
```

If `Publish failed` appears instead of `Publish finished`, the accompanying
infra snapshot (`reason: "publish_failed"`) will show what the machine
looked like at that moment.

### General log shape

Every line in `app.log` is one JSON object:

```json
{"ts": "2026-09-28T10:58:49.123Z", "level": "INFO", "logger": "pdf2webview",
 "msg": "Starting conversion", "job_id": "dc8b5753", "ref_id": null,
 "pid": 1203017, "upload_ref": "...", "attempt": 0}
```

Useful one-liners:

```bash
# All errors in the last day:
jq -c 'select(.level=="ERROR")' logs/app.log

# Every request slower than 30 seconds:
jq -c 'select(.elapsed_ms > 30000)' logs/app.log

# All infra snapshots (any reason) from a given day:
cat logs/snapshots/infra-2026-09-28.jsonl | jq .
```

(`jq` may need installing: `sudo apt install jq`. Without it, `python3 -m
json.tool` or plain `grep` on the JSON text both work.)

---

## 4. What each file/service now does

| File | Role |
|---|---|
| `python_app/app_logging.py` | Core logging + infra snapshot module (new) |
| `python_app/config.py` | `LOG_DIR`, `LOG_LEVEL`, `LOG_RETENTION_DAYS` (7), `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`, `SNAPSHOT_DIR`, `SNAPSHOT_RETENTION_DAYS` (7) — all overridable via env vars |
| `python_app/conversion_job.py` | Logs every conversion's lifecycle; snapshots on failure |
| `python_app/s3_publish.py` | Logs every publish's lifecycle; snapshots on failure; per-file upload failures now logged instead of silently propagating |
| `python_app/app.py` | Logs every HTTP request; snapshots on any unhandled exception |
| `python_app/cleanup_job.py` | Prunes old infra snapshot files (in addition to its existing job/upload TTL sweep) |
| `/etc/systemd/journald.conf` | `Storage=persistent`, `MaxRetentionSec=7day`, `SystemMaxUse=1G`, `SystemKeepFree=2G` (appended; original backed up alongside it as `journald.conf.bak.<timestamp>`) |
| `/etc/logrotate.d/pdf2html` | Daily rotation of `logs/app.log`, 7 kept, gzip-compressed |
| `pdf2html-cleanup.timer` / `.service` | New systemd timer, runs `cleanup_job.py` daily (±15min jitter), enforces the 7-day snapshot retention and the existing upload/job TTL sweep |
| `deploy/vm-logging/` | Repo copies of the three files above, for reproducibility on a fresh VM |

None of this changes `pdf2html.service`'s own `ExecStart` — the root-cause
fix in [§1](#1-root-cause-found-and-confirmed) is a separate, deliberately
deferred decision.

---

## 5. Where things live

```
/opt/pdf2html/app/
├── logs/
│   ├── app.log                     <- structured JSON app log (7-day retention)
│   └── snapshots/
│       └── infra-YYYY-MM-DD.jsonl  <- one file per day, one JSON line per snapshot
├── deploy/vm-logging/
│   ├── pdf2html.logrotate
│   ├── pdf2html-cleanup.service
│   └── pdf2html-cleanup.timer
```

```
/etc/systemd/journald.conf          <- edited (backed up first)
/etc/logrotate.d/pdf2html           <- installed
/etc/systemd/system/pdf2html-cleanup.{service,timer}  <- installed, enabled
```

---

## 6. Verifying it's working

```bash
# App log is being written:
tail -f /opt/pdf2html/app/logs/app.log

# Journald retention is active:
grep -A5 "PDF2HTML" /etc/systemd/journald.conf
systemctl is-active systemd-journald

# Logrotate config is valid:
sudo logrotate -d /etc/logrotate.d/pdf2html

# Cleanup timer is scheduled:
systemctl list-timers pdf2html-cleanup.timer
```

This was end-to-end tested on 2026-09-28 by uploading a real PDF through
`/convert` and publishing it through `/publish` against the live service,
confirming the full `job_id`/`job_dir`-correlated log trail appeared
correctly in `app.log` for both. Test artifacts (job record, output
directory, uploaded OCI objects) were cleaned up afterward.

---

## 7. Historical cleanup: 176 stuck jobs, reconciled

176 job records (of 478 total, in `python_app/jobs/*.json`) were permanently
stuck at `status: "processing"` — historical casualties of the bug in
[§1](#1-root-cause-found-and-confirmed), from before today's fix existed.

**Status: resolved.** The startup reconciliation sweep described in
[§1 "Fix applied"](#fix-applied-2026-09-28) ran automatically the first time
the fixed `pdf2html.service` started, and marked all 176 as `error` with a
clear detail message. Verified before/after:

```
Before: Counter({'processing': 176, 'published': 151, 'done': 142, 'error': 9})
After:  Counter({'error': 185, 'published': 151, 'done': 142})
```

(185 = the original 9 genuine errors + the 176 newly-reconciled ones; `done`
and `published` counts are untouched, confirming the sweep only ever touches
jobs that were actually stuck, never a legitimately-completed one.)

No manual action is needed. `cleanup_job.py`'s existing TTL sweep
(`RETENTION_TTL_DAYS`, default 30 days) will eventually expire these records
along with their (already-partial) output directories on its own.

---

## 8. Future work: the real architectural fix

Today's fix (raise `--max-requests`, add a reconciliation sweep) makes the
failure rare and, if it happens anyway, self-healing. It does not eliminate
the underlying architecture gap: conversions and publishes still run inside
the same process gunicorn manages, so *some* residual risk remains (now
requiring both an unlucky worker-recycle timing AND landing inside one of
the now much-shorter exposure windows).

This is already recognized as a known scaling blocker in
[`PRODUCTION-DEPLOYMENT-PLAN.md`](PRODUCTION-DEPLOYMENT-PLAN.md) §2.2 ("Single
Gunicorn worker... `max-requests 200` recycles the process and drops
in-flight threads"), which lays out the target fix: split the API/web tier
from a separate conversion-worker tier that pulls from a queue
(`QUEUE_BACKEND=queue`, the existing `worker.py`, an OCI Queue). That
migration is a substantially larger change (provisioning a queue, moving job
state to a shared store, etc.) and is out of scope for this fix — today's
change is a compatible stopgap for the current single-VM deployment while
that migration is pending, not a substitute for it.

---

*Reflects the application and infrastructure as of 2026-09-28, including the
gunicorn config fix and reconciliation sweep applied on that date (see §1).*
