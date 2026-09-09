# PDF2HTML — Production Deployment Plan (500 Editors)

**Author:** DevOps Lead
**Date:** 2026-09-09
**Target:** Oracle Cloud Infrastructure (OCI), region `ap-hyderabad-1`
**Application:** PDF → Interactive HTML converter (`poc-interactivetxtbk.diksha.gov.in`)
**Status:** Proposed — supersedes `docs/OCI_DEPLOYMENT.md` (which targeted ~40 users)

---

## 1. Executive Summary

The application today runs as a single Flask/Gunicorn process on one 4‑vCPU / 15 GB VM, behind Nginx, with PDF conversion executed on background threads *inside* the web worker. Job state, uploads, and rendered output all live on the VM's local disk. This is fine for a POC but will not hold 500 concurrent editors.

This plan re-architects the deployment to be **containerized (Docker)**, **fronted by OCI API Gateway**, and **horizontally scalable** on OCI Kubernetes Engine (OKE). The core change is separating the **synchronous API tier** from the **CPU-heavy conversion tier** using a **job queue**, and moving all shared state off local disk into managed OCI services.

**Headline capacity target:** 500 named editors, ~100–150 concurrent active sessions, peak of ~40–60 simultaneous PDF conversions, each 15–90s of CPU-bound work.

---

## 2. Review of Current Deployment (As-Is)

### 2.1 What is running

| Aspect | Current state |
|---|---|
| App | Flask app (`python_app/app.py`), ~18 routes, served by Gunicorn |
| Process model | `gunicorn --workers 1 --threads 4 --timeout 600`, bind `127.0.0.1:8501` |
| Reverse proxy | Nginx, `server_name poc-interactivetxtbk.diksha.gov.in`, `client_max_body_size 1200M`, HTTP :80 (no TLS in the reviewed config) |
| Conversion | Runs in a `threading.Thread` inside the web worker (docling + PaddleOCR + Whisper), CPU-bound, no GPU |
| Job state | JSON files under `python_app/jobs/*.json` (local disk) |
| Rendered output | `app/output/` (3.9 GB, local disk) served via Flask `/output/...` |
| Uploads | `python_app/uploads/` (local disk) |
| Learner progress | `learner_progress.json` / TinyDB (local file) |
| Object storage | OCI buckets via **Instance Principals**: `poc-interactivetxtbk1` (HTML), `poc-interactivetxt-media-src-bucket` (media), `poc-interactivetxt-media-dst-bucket` (video) |
| VM | 4 vCPU, 15 GB RAM, 45 GB disk (58% used), Ubuntu 22.04 |
| Dependencies | `docling`, `paddleocr`, `paddlepaddle`, `faster-whisper`, `onnxruntime`, `opencv` — venv is 7.3 GB, model cache ~4.7 GB |

### 2.2 Scaling blockers (must be fixed for 500 editors)

1. **Conversion runs inside the web worker.** A long CPU-bound job blocks the process. With one worker, one big PDF stalls everyone.
2. **File-based job state (`jobs/*.json`).** Not shared across hosts; `/api/lookup/<refId>` scans the directory — O(n) and host-local. Breaks the moment there is more than one container.
3. **Local `output/` and `uploads/` directories.** Rendered HTML is served from local disk. Two app replicas would serve different content.
4. **Local TinyDB for learner progress.** Single-writer file, host-local, not concurrency-safe at scale.
5. **Single Gunicorn worker.** No real concurrency; `max-requests 200` recycles the process (and drops in-flight threads).
6. **No API Gateway.** No central auth, throttling, quotas, or routing.
7. **No TLS at the edge** in the reviewed Nginx config.
8. **One monolithic 7 GB+ ML image footprint** mixed with the web tier — slow to scale, wasteful for pure-API traffic.

---

## 3. Target Architecture (To-Be)

### 3.1 Principles

- **Split the tiers.** A stateless **API/web tier** handles HTTP fast; a separate **conversion worker tier** does the heavy ML work, pulled from a queue.
- **No local state.** All shared state moves to managed OCI services (Object Storage, Redis/Queue, Autonomous DB).
- **Everything containerized.** Reproducible images in OCI Container Registry (OCIR), orchestrated by OKE.
- **All APIs behind OCI API Gateway.** Single ingress for auth, throttling, quotas, CORS, and routing.
- **Scale independently.** API pods scale on request rate; workers scale on queue depth.

### 3.2 Architecture diagram

```
                         Internet (500 editors + learners)
                                      │  HTTPS 443
                          ┌───────────▼────────────┐
                          │      OCI WAF            │  OWASP rules, rate limit
                          └───────────┬────────────┘
                          ┌───────────▼────────────┐
                          │    OCI API Gateway      │  TLS term, JWT auth,
                          │  (routes + throttling)  │  usage plans, CORS
                          └───────────┬────────────┘
                    ┌─────────────────┼──────────────────┐
                    │                 │                  │
        (sync APIs) │        (uploads)│         (static/rendered)
          ┌─────────▼─────────┐  ┌────▼──────────┐  ┌────▼────────────────┐
          │  OKE: API Tier    │  │ OKE: API Tier │  │  OCI Object Storage  │
          │  Flask (gunicorn) │  │ (multipart)   │  │  + CDN (edge cache)  │
          │  Deployment + HPA │  │               │  │  HTML / media / HLS  │
          └─────────┬─────────┘  └────┬──────────┘  └──────────────────────┘
                    │  enqueue job     │  put object
          ┌─────────▼──────────────────▼─────────┐
          │   OCI Queue  /  Redis (job broker)    │
          └─────────┬─────────────────────────────┘
                    │  dequeue
          ┌─────────▼───────────────────────────┐
          │   OKE: Conversion Worker Tier        │  docling+PaddleOCR+Whisper
          │   Deployment + KEDA (scale on queue) │  CPU-heavy, own node pool
          └─────────┬───────────────────────────┘
                    │ write state / results
          ┌─────────▼─────────┐   ┌────────────────────────┐
          │ OCI Cache (Redis) │   │ OCI Autonomous JSON DB  │
          │ job status/state  │   │ jobs, refId, progress   │
          └───────────────────┘   └────────────────────────┘
```

### 3.3 Component responsibilities

| Tier | Runs | Scales on | Notes |
|---|---|---|---|
| **API / Web** | Flask via Gunicorn (`gevent`/threads), no ML libs | Request rate / CPU (HPA) | Handles `/convert` enqueue, `/convert-status`, `/api/lookup`, `/publish` trigger, sections, glossary. Stateless. |
| **Conversion Worker** | Consumer process: docling + PaddleOCR + Whisper | Queue depth (KEDA) | Pulls jobs, converts, writes result to Object Storage + DB. Dedicated node pool with more RAM. |
| **Broker** | OCI Queue (or Redis Streams) | — | Decouples enqueue from processing; enables backpressure. |
| **State store** | OCI Cache (Redis) for hot job status; Autonomous JSON DB for durable job/refId/progress records | Managed | Replaces `jobs/*.json` and TinyDB. |
| **Object Storage + CDN** | Rendered HTML, media, HLS | Managed | Replaces local `output/`. `/output/...` served from Object Storage via CDN, not from a pod. |

---

## 4. Required Application Changes

These are prerequisites, not optional. Containerizing without them just moves the same single-host bottlenecks into pods.

### 4.1 Move conversion to a queue-backed worker
Replace the in-process `threading.Thread(target=run_conversion)` in `/convert` with an enqueue call. `/convert` becomes: save upload to Object Storage → push a job message (`job_id`, `ref_id`, object key) → return `202 {job_id}`. The worker consumes the message and runs `convert_pdf_to_html`.

### 4.2 Externalize job state
Replace `_read_job` / `_write_job` (file-based) with:
- **Redis** for live status/stage/detail (fast polling from `/convert-status` and `/api/lookup`).
- **Autonomous JSON DB** as the durable record, indexed by both `job_id` and `ref_id` (so `/api/lookup/<refId>` is an indexed query, not a directory scan).

### 4.3 Serve rendered output from Object Storage
The `/output/<job_dir>/<file>` GET should redirect to (or be fronted by) the CDN/Object Storage URL rather than `send_from_directory`. The editor **save** (`PUT /output/...`) writes back to Object Storage. This removes the local-disk dependency that blocks running multiple replicas.

### 4.4 Externalize learner progress
Move `learner_progress.json` / TinyDB to the Autonomous JSON DB (or Redis with persistence). Progress writes must be concurrency-safe.

### 4.5 Config via environment / OCI Vault
Bucket names, region, DB connection string, queue/Redis endpoints, and CMS tokens come from env vars, with secrets in **OCI Vault**. Keep **Instance/Workload Principals** for OCI auth — no keys on disk (already the pattern in `oci_storage.py`).

> These changes are backward-compatible behind the existing route contract in `DOCLING-INTEGRATION-REPORT.md`, so the parent CMS integration (`/api/lookup/<refId>`, `?refId=`, `/publish`) does not change.

---

## 5. Dockerization

Two images from one repo: a lightweight **API image** and a heavy **worker image**. This keeps API pods small and fast to scale while isolating the 7 GB ML stack in the worker.

### 5.1 Worker image (heavy — ML stack)

```dockerfile
# Dockerfile.worker
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HOME=/models PADDLE_HOME=/models/paddle

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgl1 libglib2.0-0 poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY python_app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-bake ML models into the image so pods start warm (no first-request
# download). Models are otherwise ~4.7 GB fetched at runtime.
COPY scripts/prefetch_models.py .
RUN python prefetch_models.py

COPY python_app/ /app/
CMD ["python", "worker.py"]
```

### 5.2 API image (light — no ML libs)

```dockerfile
# Dockerfile.api
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY python_app/requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt gunicorn gevent
COPY python_app/ /app/
EXPOSE 8501
# gevent workers for I/O-bound API traffic; conversion no longer runs here
CMD ["gunicorn", "--workers", "4", "--worker-class", "gevent", \
     "--worker-connections", "200", "--timeout", "120", \
     "--bind", "0.0.0.0:8501", "app:app"]
```

`requirements-api.txt` = the current `requirements.txt` minus `docling`, `paddleocr`, `paddlepaddle`, `faster-whisper`, `onnxruntime`, `opencv-python` (those stay in the worker).

### 5.3 Local composition for dev / CI (`docker-compose.yml`)

```yaml
services:
  api:
    build: { context: ., dockerfile: Dockerfile.api }
    ports: ["8501:8501"]
    environment:
      REDIS_URL: redis://redis:6379/0
      QUEUE_NAME: pdf-convert
    depends_on: [redis]
  worker:
    build: { context: ., dockerfile: Dockerfile.worker }
    environment:
      REDIS_URL: redis://redis:6379/0
      QUEUE_NAME: pdf-convert
    deploy: { replicas: 2 }
    depends_on: [redis]
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
```

Images are built in CI and pushed to **OCIR** (`<region>.ocir.io/<tenancy-namespace>/pdf2html/api` and `.../worker`), tagged with the git SHA.

---

## 6. OCI API Gateway

All routes are published through a single API Gateway deployment. It becomes the only public entry point; OKE services stay private.

### 6.1 Routing

| Path | Backend | Method(s) | Auth | Throttle |
|---|---|---|---|---|
| `/convert` | API service | POST | JWT (editor) | 10 / min / user |
| `/convert-status/{job_id}` | API service | GET | JWT | 60 / min / user |
| `/upload-media/{job_dir}` | API service | POST | JWT | 20 / min / user |
| `/publish` | API service | POST | JWT | 10 / min / user |
| `/export-cms` | API service | POST | JWT | 5 / min / user |
| `/api/lookup/{refId}` | API service | GET | mTLS / service token | server-to-server |
| `/api/sections/*`, `/api/glossary-highlight`, `/api/make-interactive`, `/api/label-info`, `/api/progress`, `/api/generate-captions` | API service | GET/POST | JWT | per-route |
| `/output/{job_dir}/{file}` (GET) | Object Storage / CDN | GET | public/PAR | CDN-cached |
| `/output/{job_dir}/{file}` (PUT save) | API service | PUT | JWT (editor) | 30 / min / user |

### 6.2 Gateway features to enable

- **TLS termination** with an OCI-managed certificate for `poc-interactivetxtbk.diksha.gov.in`.
- **JWT validation** (OCI IAM / IDCS or the CMS identity provider) so editor identity is enforced at the edge.
- **Usage plans & quotas** — per-editor rate limits protect the CPU-bound worker tier from a stampede.
- **Request/response limits** — keep the `1200M` body limit for large media uploads on the relevant routes only; keep other routes small.
- **CORS** — allow the CMS origin for iframe integration.
- **Logging & metrics** to OCI Logging and Monitoring.

---

## 7. OKE (Kubernetes) Deployment

### 7.1 Cluster and node pools

| Node pool | Shape | Nodes (min→max) | Purpose |
|---|---|---|---|
| `api-pool` | VM.Standard.E4.Flex 2 OCPU / 16 GB | 3 → 8 | API/web pods (stateless) |
| `worker-pool` | VM.Standard.E4.Flex 4 OCPU / 32 GB | 3 → 20 | Conversion workers (CPU/RAM heavy) |
| `system-pool` | VM.Standard.E4.Flex 1 OCPU / 8 GB | 2 | ingress, metrics, KEDA, cluster autoscaler |

Cluster Autoscaler resizes node pools; pods scale within them.

### 7.2 API autoscaling (HPA)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: { name: api-hpa }
spec:
  scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: pdf2html-api }
  minReplicas: 3
  maxReplicas: 20
  metrics:
    - type: Resource
      resource: { name: cpu, target: { type: Utilization, averageUtilization: 60 } }
```

### 7.3 Worker autoscaling on queue depth (KEDA)

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata: { name: worker-scaler }
spec:
  scaleTargetRef: { name: pdf2html-worker }
  minReplicaCount: 2
  maxReplicaCount: 40
  triggers:
    - type: redis
      metadata:
        address: <redis-host>:6379
        listName: pdf-convert
        listLength: "3"   # add a worker per ~3 queued jobs
```

Each worker pod requests ~4 GB RAM / 1.5 CPU (docling + OCR peak). Models are baked into the image, so pods start warm.

### 7.4 Sizing for 500 editors

- **Assumption:** 500 named editors → ~100–150 concurrent sessions → peak ~40–60 conversions enqueued in a burst; steady state ~10–20 conversions/min.
- **Worker throughput:** ~1 conversion / 45s / worker average → ~1.3 conversions/min/worker. To clear ~20/min steady requires ~15 workers; burst scales toward the `maxReplicaCount: 40` cap. This is why conversion is queue-decoupled — bursts queue instead of failing.
- **API tier:** polling (`/convert-status`, `/api/lookup`) dominates request volume. gevent workers + HPA at 60% CPU handle this comfortably at 3–20 pods.

---

## 8. Data & State Services

| Concern | Service | Replaces |
|---|---|---|
| Live job status (hot, polled) | **OCI Cache with Redis** | `jobs/*.json` reads |
| Durable job / refId records | **OCI Autonomous JSON DB** | `jobs/*.json`, directory scan in `/api/lookup` |
| Learner progress | **Autonomous JSON DB** | `learner_progress.json` (TinyDB) |
| Rendered HTML / media / video | **OCI Object Storage** (existing 3 buckets) + **CDN** | local `output/` |
| Job queue / broker | **OCI Queue** or **Redis Streams** | in-process `threading.Thread` |
| Secrets | **OCI Vault** | env/plain files |

Bucket lifecycle: auto-expire `uploads` after 24h; transition rendered output to Infrequent Access after 90 days.

---

## 9. Security

| Control | Implementation |
|---|---|
| Edge TLS | TLS 1.3 at API Gateway (OCI-managed cert) |
| Auth | JWT validation at Gateway; `/api/lookup` restricted to service caller (mTLS/token) |
| OCI auth | **Workload Identity / Instance Principals** in OKE — no keys on disk |
| Secrets | OCI Vault, mounted via CSI / injected as env |
| Network | OKE nodes in private subnets; only API Gateway + LB public |
| WAF | OCI WAF (OWASP) in front of the Gateway |
| Upload validation | File type + magic-byte check server-side; per-type size limits (already in `app.py`) |
| Rate limiting | API Gateway usage plans per editor |
| Path traversal | Keep existing `..` and `relative_to(OUTPUT_DIR)` guards |

---

## 10. CI/CD

1. **Build:** on merge to `main`, CI builds `Dockerfile.api` and `Dockerfile.worker`, tags with git SHA, pushes to OCIR.
2. **Scan:** image vulnerability scan (OCIR scanning) gates the release.
3. **Deploy:** `kubectl`/Helm applies manifests to OKE; rolling update with `maxSurge`/`maxUnavailable` for zero-downtime.
4. **Migrate:** run DB migration job (create JSON collections/indexes) before switching traffic.
5. **Promote:** blue/green at the Gateway — new deployment gets a fraction of traffic, then full cutover.
6. **Rollback:** re-point Gateway backend / `kubectl rollout undo`.

---

## 11. Observability

| Signal | Tool | Alert |
|---|---|---|
| API latency / 5xx rate | OCI APM + Monitoring | 5xx > 1% for 5 min |
| Queue depth | Redis/OCI Queue metric | depth > 100 sustained |
| Worker conversion failures | App metric → Monitoring | failure rate > 5% |
| Conversion duration p95 | App metric | p95 > 180s |
| Node/pod CPU & memory | OKE metrics | node CPU > 80% for 5 min |
| Object Storage / DB errors | OCI Logging | any spike |

Centralize logs in OCI Logging; dashboards in OCI Monitoring; traces via APM across Gateway → API → worker.

---

## 12. Rollout Plan

| Phase | Work | Duration |
|---|---|---|
| **0. Prereq refactor** | Queue-backed worker, external state (Redis + JSON DB), serve output from Object Storage | 1–2 weeks |
| **1. Containerize** | Build API + worker images, docker-compose, push to OCIR | 3 days |
| **2. Provision OCI** | OKE cluster + node pools, OCI Cache, Autonomous JSON DB, Queue, Vault, API Gateway, WAF, CDN | 3 days |
| **3. Deploy to staging** | Helm deploy, wire Gateway routes, load test | 3 days |
| **4. Load test** | Simulate 500 editors / 60 concurrent conversions; tune HPA/KEDA | 2–3 days |
| **5. Cutover** | DNS to API Gateway, blue/green, monitor, decommission old VM | 1 day |

### 12.1 Load-test exit criteria
- 500 simulated editors, 60 concurrent conversions: **p95 conversion < 120s**, API **p95 < 500ms**, **error rate < 1%**, no queue starvation, autoscalers stabilize.

---

## 13. Cost Estimate (Monthly, indicative, `ap-hyderabad-1`)

| Service | Spec | Est. (USD) |
|---|---|---|
| OKE control plane | Basic | free / minimal |
| API node pool | 3–8 × E4.Flex 2 OCPU/16 GB (avg ~4) | ~$380 |
| Worker node pool | 3–20 × E4.Flex 4 OCPU/32 GB (avg ~8) | ~$1,500 |
| OCI Cache (Redis) | HA small | ~$150 |
| Autonomous JSON DB | 2 OCPU / 1 TB | ~$260 |
| API Gateway | per-call + hours | ~$80 |
| Object Storage + CDN | ~2 TB + egress | ~$120 |
| WAF | edge policy | ~$40 |
| Load Balancer / networking | flexible LB | ~$60 |
| **Total** | | **~$2,600 / month** |

Cost scales with actual conversion volume because the worker pool is the dominant line item and is autoscaled — off-peak it drops toward the `min` node count.

---

## 14. Summary of Recommendations

1. **Decouple conversion from the web tier** via a queue — the single most important change.
2. **Two Docker images** (light API, heavy worker) pushed to OCIR.
3. **Front all APIs with OCI API Gateway** for TLS, JWT auth, throttling, and quotas.
4. **Move all state off local disk** to OCI Cache (Redis), Autonomous JSON DB, and Object Storage + CDN.
5. **Run on OKE** with HPA (API on CPU) and KEDA (workers on queue depth) for independent, elastic scaling to 500 editors.
6. **Keep the existing route contract and Instance-Principal OCI auth** so the CMS integration and buckets are unchanged.
