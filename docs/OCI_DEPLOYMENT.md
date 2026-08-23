# PDF2HTML with ML — OCI Deployment Architecture

## Executive Summary

Deploy the PDF-to-HTML conversion application on Oracle Cloud Infrastructure (OCI) for ~40 concurrent editor users. Media files (images, videos, audio, H5P, PPTX) stored in OCI Object Storage. Videos converted to adaptive streaming (HLS) via OCI Media Flow.

---

## Architecture Diagram

```
                    ┌─────────────────────────────────────────┐
                    │            OCI Load Balancer             │
                    │         (Public IP, HTTPS/443)           │
                    └──────────────────┬──────────────────────┘
                                       │
                    ┌──────────────────┴──────────────────────┐
                    │         OCI Compute (App Tier)           │
                    │                                          │
                    │  ┌──────────────────────────────────┐   │
                    │  │   VM 1: Flask App (Gunicorn)      │   │
                    │  │   - PDF conversion (Docling)      │   │
                    │  │   - RapidOCR / PaddleOCR          │   │
                    │  │   - Whisper (captions)            │   │
                    │  │   - Flask API server              │   │
                    │  └──────────────────────────────────┘   │
                    │                                          │
                    │  ┌──────────────────────────────────┐   │
                    │  │   VM 2: Worker (async jobs)        │   │
                    │  │   - Video transcoding (ffmpeg)    │   │
                    │  │   - HLS packaging                 │   │
                    │  │   - Caption generation            │   │
                    │  │   - Strapi export (bulk)          │   │
                    │  └──────────────────────────────────┘   │
                    └──────────────────┬──────────────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
    ┌─────────┴────────┐   ┌──────────┴──────────┐   ┌────────┴────────┐
    │  OCI Object      │   │   OCI Media Flow    │   │  OCI Autonomous │
    │  Storage          │   │   (Video Streaming) │   │  JSON DB        │
    │                   │   │                     │   │                 │
    │  - PDF uploads    │   │  - Transcode MP4    │   │  - User progress│
    │  - Converted HTML │   │    → HLS (m3u8)     │   │  - Session data │
    │  - Images/media   │   │  - Adaptive bitrate │   │  - Export logs  │
    │  - Video source   │   │  - CDN delivery     │   │                 │
    └──────────────────┘   └─────────────────────┘   └─────────────────┘
              │
    ┌─────────┴────────┐
    │  OCI CDN          │
    │  (Edge caching)   │
    │  - Static HTML    │
    │  - Images         │
    │  - HLS streams    │
    └──────────────────┘
```

---

## Infrastructure Specifications

### Compute Instances

| Component | Shape | OCPUs | RAM | Storage | Quantity |
|-----------|-------|-------|-----|---------|----------|
| App Server | VM.Standard.E4.Flex | 4 | 32 GB | 100 GB Boot | 1 |
| Worker Server | VM.Standard.E4.Flex | 4 | 32 GB | 200 GB Boot | 1 |

**Justification:**
- Docling AI models require ~4 GB RAM loaded
- PaddleOCR + Whisper need ~8 GB during inference
- 4 OCPUs handle 3-4 concurrent PDF conversions (each takes 15-90s)
- 40 users won't all convert simultaneously — peak estimated at 5-8 concurrent

### Object Storage

| Bucket | Purpose | Estimated Size | Tier |
|--------|---------|----------------|------|
| `pdf2html-uploads` | Incoming PDF files (temporary) | 10 GB | Standard |
| `pdf2html-output` | Converted HTML + images | 200 GB | Standard |
| `pdf2html-media` | Video/Audio/H5P/PPTX uploads | 500 GB | Standard |
| `pdf2html-streaming` | HLS transcoded videos (m3u8 + segments) | 1 TB | Standard |

**Lifecycle Rules:**
- `pdf2html-uploads`: auto-delete after 24 hours (temporary holding)
- `pdf2html-streaming`: move to Infrequent Access after 90 days

### Video Streaming (OCI Media Flow)

| Setting | Value |
|---------|-------|
| Transcoding profiles | 360p, 720p, 1080p |
| Output format | HLS (m3u8 + TS segments) |
| Segment duration | 6 seconds |
| DRM | None (internal use) |
| Trigger | Object Storage event on video upload |

**Flow:**
1. Editor uploads video → stored in `pdf2html-media`
2. Object Storage Event triggers Media Flow job
3. Media Flow transcodes to HLS (3 quality tiers)
4. Output goes to `pdf2html-streaming`
5. CDN-cached streaming URL returned to the app
6. Video player uses HLS.js for adaptive playback

### Database (Learner Progress)

| Service | Spec |
|---------|------|
| OCI Autonomous JSON Database | 1 OCPU, 20 GB storage |

Replaces local TinyDB. Stores:
- Video playback progress per learner
- Export audit logs
- Session metadata

### Networking

| Component | Configuration |
|-----------|---------------|
| VCN | 10.0.0.0/16, 1 public subnet, 1 private subnet |
| Load Balancer | Public subnet, 100 Mbps, HTTPS termination |
| App/Worker VMs | Private subnet |
| NAT Gateway | For private subnet outbound (Strapi API, Wikipedia API) |
| Security Lists | 443 inbound to LB, 8501 internal, 22 SSH from bastion only |

### CDN (Content Delivery)

| Setting | Value |
|---------|-------|
| OCI Edge Services | Enabled on Object Storage buckets |
| Cache rules | HTML: 1h TTL, Images: 7d TTL, HLS: 24h TTL |
| Origin | Object Storage public bucket URLs |

---

## Application Changes for OCI

### 1. Object Storage Integration

Replace local file storage with OCI Object Storage SDK:

```python
# python_app/storage.py
import oci

config = oci.config.from_file()  # Uses instance principal in production
object_storage = oci.object_storage.ObjectStorageClient(config)
namespace = object_storage.get_namespace().data

def upload_to_bucket(bucket: str, filename: str, data: bytes) -> str:
    object_storage.put_object(namespace, bucket, filename, data)
    return f"https://objectstorage.{region}.oraclecloud.com/n/{namespace}/b/{bucket}/o/{filename}"

def get_presigned_url(bucket: str, filename: str, expiry_hours: int = 24) -> str:
    par = oci.object_storage.models.CreatePreauthenticatedRequestDetails(
        name=f"par-{filename}",
        access_type="ObjectRead",
        time_expires=datetime.utcnow() + timedelta(hours=expiry_hours),
        object_name=filename,
    )
    resp = object_storage.create_preauthenticated_request(namespace, bucket, par)
    return f"https://objectstorage.{region}.oraclecloud.com{resp.data.access_uri}"
```

### 2. Video Streaming URL

After Media Flow transcodes, the streaming URL format:

```
https://objectstorage.<region>.oraclecloud.com/n/<namespace>/b/pdf2html-streaming/o/<video_id>/master.m3u8
```

The HTML template should switch from `<video src="...">` to HLS.js:

```html
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<video id="player"></video>
<script>
  if (Hls.isSupported()) {
    var hls = new Hls();
    hls.loadSource('https://.../<video_id>/master.m3u8');
    hls.attachMedia(document.getElementById('player'));
  }
</script>
```

### 3. Environment Variables

```bash
# OCI Configuration
OCI_REGION=ap-mumbai-1
OCI_NAMESPACE=<tenancy_namespace>
OCI_COMPARTMENT_ID=ocid1.compartment.oc1..xxxxx

# Buckets
BUCKET_UPLOADS=pdf2html-uploads
BUCKET_OUTPUT=pdf2html-output
BUCKET_MEDIA=pdf2html-media
BUCKET_STREAMING=pdf2html-streaming

# Database
DB_CONNECTION_STRING=<autonomous_db_connection_string>

# App
FLASK_ENV=production
GUNICORN_WORKERS=4
MAX_UPLOAD_SIZE_MB=1200

# Strapi
STRAPI_BASE_URL=https://strapi.diksha.gov.in
```

---

## Deployment Plan

### Phase 1: Infrastructure Setup (Day 1-2)

1. **Create VCN** with public/private subnets
2. **Provision Compute VMs** (App + Worker)
3. **Create Object Storage buckets** (4 buckets)
4. **Set up Load Balancer** with SSL certificate
5. **Create Autonomous JSON Database**
6. **Configure Media Flow** workflow for video transcoding
7. **Set up OCI Events** to trigger Media Flow on video upload

### Phase 2: Application Deployment (Day 3-4)

1. **Install dependencies** on App VM:
   ```bash
   sudo apt update && sudo apt install -y python3.11 python3.11-venv ffmpeg
   python3.11 -m venv /opt/pdf2html/venv
   source /opt/pdf2html/venv/bin/activate
   pip install -r requirements.txt
   pip install gunicorn oci
   ```

2. **Deploy application code**:
   ```bash
   git clone https://github.com/anandarup/PDF2HTML_with_ML.git /opt/pdf2html/app
   ```

3. **Configure Gunicorn** (`/etc/systemd/system/pdf2html.service`):
   ```ini
   [Unit]
   Description=PDF2HTML Flask App
   After=network.target

   [Service]
   User=pdf2html
   WorkingDirectory=/opt/pdf2html/app/python_app
   Environment="PATH=/opt/pdf2html/venv/bin"
   ExecStart=/opt/pdf2html/venv/bin/gunicorn \
     --workers 4 \
     --timeout 600 \
     --bind 0.0.0.0:8501 \
     --max-requests 100 \
     --max-requests-jitter 20 \
     app:app
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

4. **Configure Worker** (Celery or similar for async jobs):
   ```bash
   # Video transcoding, caption generation, bulk exports
   celery -A worker worker --concurrency=2
   ```

5. **Configure Nginx** reverse proxy on LB:
   ```nginx
   location / {
       proxy_pass http://app-vm:8501;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       client_max_body_size 1200M;
       proxy_read_timeout 600s;
   }
   ```

### Phase 3: Testing & Cutover (Day 5)

1. Run test conversions with all PDF samples
2. Test media upload → Object Storage → CDN delivery
3. Test video upload → Media Flow → HLS streaming
4. Test Strapi export with media
5. Load test: 10 concurrent conversions
6. DNS cutover

---

## Security

| Control | Implementation |
|---------|----------------|
| HTTPS | TLS 1.3 on Load Balancer (OCI-managed cert) |
| Auth | Instance Principal for OCI SDK (no keys on disk) |
| Network | App VMs in private subnet, SSH via bastion only |
| Secrets | OCI Vault for DB credentials, Strapi JWT |
| Upload validation | File type + magic bytes check server-side |
| Rate limiting | Nginx `limit_req` (10 conversions/min/user) |
| WAF | OCI WAF on Load Balancer (OWASP rules) |

---

## Cost Estimate (Monthly)

| Service | Spec | Est. Cost (USD) |
|---------|------|-----------------|
| Compute (App VM) | E4.Flex 4 OCPU / 32 GB | $95 |
| Compute (Worker VM) | E4.Flex 4 OCPU / 32 GB | $95 |
| Object Storage | ~1.7 TB total | $40 |
| Load Balancer | 100 Mbps | $20 |
| Autonomous JSON DB | 1 OCPU / 20 GB | $70 |
| Media Flow | ~100 videos/month, 3 profiles | $50 |
| CDN / Data Transfer | ~500 GB/month | $40 |
| **Total** | | **~$410/month** |

*Based on OCI pay-as-you-go pricing for ap-mumbai-1 region.*

---

## Scaling Considerations

For 40 users, the above is sufficient. If usage grows:

| Threshold | Action |
|-----------|--------|
| >10 concurrent conversions | Add second App VM behind LB |
| >100 GB video/month | Enable OCI Media Services (managed streaming) |
| >100 users | Move to OKE (Kubernetes) with autoscaling pods |
| Global users | Multi-region Object Storage replication + CDN |

---

## Monitoring

| Metric | Tool | Alert |
|--------|------|-------|
| CPU > 80% sustained 5min | OCI Monitoring | Email + PagerDuty |
| Conversion queue > 10 | Custom metric | Slack webhook |
| 5xx error rate > 1% | OCI Logging / Analytics | Email |
| Storage > 80% capacity | OCI Monitoring | Email |
| Media Flow job failure | OCI Events | Email |

---

## Handover Checklist for DevOps

- [ ] OCI tenancy access with Admin privileges
- [ ] Compartment for pdf2html resources
- [ ] SSH key pair for VM access
- [ ] Domain name + DNS management access
- [ ] SSL certificate (or use OCI-managed Let's Encrypt)
- [ ] GitHub repository access for deployment
- [ ] Strapi endpoint URL + admin credentials (for export testing)
- [ ] OCI budget alerts configured
