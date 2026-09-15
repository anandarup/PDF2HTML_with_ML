# PDF2WebView — Scalable Deployment (Phases 5–7)

This directory contains the containerization and OCI/OKE deployment artifacts
for the scalable architecture (Candidate B: API / Queue / Worker + State
Service). See `.kiro/specs/scalable-deployment/` for requirements, the
architecture selection, and the full task plan.

## Images

Two images are built from the repo root:

| Image | Dockerfile | Contents | Scales on |
|---|---|---|---|
| `pdf2html/api` | `Dockerfile.api` | Lean: Flask + gevent, no ML libs (`requirements-api.txt`) | CPU / request rate (HPA) |
| `pdf2html/worker` | `Dockerfile.worker` | Full ML stack + pre-baked models (`requirements.txt`) | Queue depth (KEDA) |

The API image can omit the ML stack because `app.py` imports the conversion
code lazily — under `QUEUE_BACKEND=queue` the API only enqueues; the worker
runs the pipeline.

## Local dev

```bash
docker compose up --build     # api + worker + redis + minio
```
Set `QUEUE_BACKEND=thread` on the api service for a pure single-process run
(worker idle, API converts in-process — identical to the legacy VM behavior).

## Build & push to OCIR

```bash
SHA=$(git rev-parse --short HEAD)
REG=<region>.ocir.io/<tenancy-namespace>/pdf2html
docker build -f Dockerfile.api    -t $REG/api:$SHA .
docker build -f Dockerfile.worker -t $REG/worker:$SHA .
docker push $REG/api:$SHA
docker push $REG/worker:$SHA
```

## Provision (once)

- OKE cluster with node pools `api-pool` (E4.Flex 2 OCPU/16 GB) and
  `worker-pool` (E4.Flex 4 OCPU/32 GB); install KEDA + cluster autoscaler.
- OCI Cache with Redis; Autonomous JSON DB (index `job_id`, `ref_id`).
- OCI Queue (`pdf-convert`); note its OCID + data-plane endpoint.
- Object Storage buckets already exist (HTML/media/video) + CDN edge.
- Workload Identity: bind `pdf2html-workload` SA to a dynamic group with
  policies for Object Storage + Queue.
- OCI Vault: store `REDIS_URL`, `JSONDB_URL`, `QUEUE_OCID`, `QUEUE_ENDPOINT`,
  `STRAPI_API_TOKEN`; sync into the `pdf2html-secrets` Secret.

## Deploy

```bash
kubectl apply -f deploy/00-namespace.yaml
kubectl apply -f deploy/10-config.yaml
# 11-secrets: synced from Vault (do NOT apply the example with real creds)
kubectl apply -f deploy/20-api-deployment.yaml
kubectl apply -f deploy/30-worker-deployment.yaml
kubectl apply -f deploy/50-cleanup-cronjob.yaml
# API Gateway (40-api-gateway.yaml) applied via OCI API Gateway API/Terraform.
```

## Migrate state (cutover)

```bash
# import existing file-based jobs into the service store
STATE_BACKEND=service REDIS_URL=... python migrate_jobs_to_service.py --dry-run
STATE_BACKEND=service REDIS_URL=... python migrate_jobs_to_service.py
```

## Cutover & rollback

1. Deploy to OKE alongside the existing VM (both live).
2. Point the API Gateway at the OKE backend for a fraction of traffic; watch
   metrics (`/metrics`, queue depth, conversion p95, 5xx).
3. Full DNS/gateway cutover once validated.
4. Rollback = re-point the gateway to the old VM (kept until sign-off).

## Config flags (env)

| Flag | Prod value | Notes |
|---|---|---|
| `STATE_BACKEND` | `service` | Redis + JSON DB |
| `OUTPUT_BACKEND` | `oci` | Object Storage + CDN |
| `QUEUE_BACKEND` | `queue` | OCI Queue + worker.py |

All three default to the legacy single-VM values (`file`/`local`/`thread`), so
the same code runs unchanged on the old VM during migration.

## Observability

- `/healthz` (liveness), `/readyz` (readiness incl. backend checks).
- `/metrics` (JSON: queue depth + active backends), `/metrics/queue-depth`
  (KEDA metrics-api source).
- Alert on: queue depth sustained high, conversion failure rate, API 5xx rate,
  node CPU (via OCI Monitoring / APM).

## Lifecycle

`cleanup_job.py` (nightly CronJob) expires transient uploads for terminal jobs
and old **unpublished** job records + output. It never deletes published
learner content.
