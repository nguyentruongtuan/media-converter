# Self-Hosted Video Transcoding Platform — Task Checklist

Goal: Build an AWS MediaConvert-like video transcoding platform entirely on Kubernetes, using FastAPI, FFmpeg, Celery, Redis, PostgreSQL, MinIO, NGINX and KEDA.

## Architecture

```text
Client -> FastAPI -> PostgreSQL (metadata)
                  -> MinIO (original videos)
                  -> Redis (queue) -> Celery Workers -> FFmpeg -> MinIO (outputs) -> NGINX -> Video Player
```

## Phase 1 — Project Initialization
- [x] Create Python project structure
- [x] Configure FastAPI application
- [x] Configure environment variables and settings
- [x] Create Dockerfile for API and worker
- [x] Create Docker Compose for local development
- [x] Configure logging and error handling
- [x] Implement health check endpoint

## Phase 2 — Database & Storage
- [x] Deploy PostgreSQL
- [x] Design video and transcoding job database models
- [x] Configure SQLAlchemy and Alembic migrations
- [x] Deploy MinIO with persistent storage
- [x] Create source and output buckets
- [x] Implement MinIO Python storage service
- [x] Implement presigned upload and download URLs

## Phase 3 — Video Upload API
- [x] Implement create-video endpoint
- [x] Generate presigned upload URL
- [x] Implement upload completion endpoint with object validation
- [x] Validate video metadata using FFprobe
- [x] Store video metadata in PostgreSQL
- [x] Implement video listing and detail endpoints
- [x] Implement video deletion and storage cleanup

## Phase 4 — Background Job Processing
- [x] Deploy Redis
- [x] Configure Celery application
- [x] Implement transcoding job creation API
- [x] Implement Celery transcoding task
- [x] Implement job status tracking
- [x] Implement retry and timeout handling
- [x] Prevent duplicate processing using idempotency
- [x] Implement job failure handling and recovery

## Phase 5 — FFmpeg Transcoding Engine
- [x] Create FFmpeg Python service
- [x] Implement resolution presets: 1080p, 720p, 480p, 360p
- [x] Implement H.264 and AAC encoding
- [x] Preserve original aspect ratio
- [x] Prevent unnecessary video upscaling
- [x] Implement configurable bitrate and quality
- [x] Implement video thumbnail generation
- [x] Upload transcoded outputs to MinIO
- [x] Capture FFmpeg progress and errors

## Phase 6 — Adaptive Streaming
- [x] Implement HLS video generation
- [x] Generate multiple bitrate variants
- [x] Generate master.m3u8 playlist
- [x] Implement HLS segment storage
- [x] Configure correct MIME types
- [x] Implement adaptive streaming playback
- [ ] Verify compatibility with iOS, Android and browsers — Chromium/hls.js verified; iOS Safari and Android still to check (docs/testing.md)

## Phase 7 — Kubernetes Deployment

Manifests in `deploy/k8s` are written and schema-validated (kubeconform), not yet applied to a cluster.

- [x] Create dedicated Kubernetes namespace
- [x] Create ConfigMaps and Secrets
- [x] Deploy PostgreSQL and persistent storage
- [x] Deploy Redis
- [x] Deploy MinIO with persistent storage
- [x] Deploy FastAPI Deployment and Service
- [x] Deploy Celery worker Deployment
- [x] Configure CPU and memory requests/limits
- [x] Configure Ingress and HTTPS
- [x] Implement readiness and liveness probes
- [x] Configure temporary transcoding disk space

## Phase 8 — Autoscaling & Reliability
- [x] Install and configure KEDA — scripts/install-keda.sh + deploy/k8s/keda.yaml (not yet applied to a cluster)
- [x] Configure Redis queue-based worker scaling
- [x] Set worker concurrency limits
- [x] Configure graceful worker shutdown
- [x] Implement retry policies and dead-letter handling
- [x] Handle interrupted transcoding jobs
- [x] Configure maximum concurrent transcoding jobs
- [ ] Test scaling and recovery under load — recovery verified on Compose (scripts/chaos_test.py); KEDA scaling needs a cluster

## Phase 9 — Monitoring & Security
- [x] Expose Prometheus metrics
- [x] Create Grafana dashboard
- [x] Configure centralized application logs
- [x] Monitor FFmpeg CPU, memory and processing time
- [x] Implement authentication and API authorization
- [x] Validate uploads and enforce size limits
- [x] Configure MinIO bucket access policies
- [x] Configure alerts for failed jobs and storage capacity

## Phase 10 — Testing & Production Release
- [x] Write API unit and integration tests
- [x] Test FFmpeg transcoding presets
- [ ] Test HLS playback on different devices — procedure and matrix in docs/testing.md; only Chromium checked so far
- [x] Test invalid and corrupted video uploads
- [x] Test concurrent processing
- [ ] Test Kubernetes worker restart recovery — same script with a kubectl kill command; verified on Compose only
- [ ] Perform load and performance testing — tests/load/locustfile.py ready; not yet run at scale
- [x] Configure database and object storage backups
- [x] Create deployment and operational documentation
- [ ] Release production MVP — CI release job + checklist in docs/operations.md; needs a cluster and real hostnames

## MVP Acceptance Criteria

- Upload video directly to MinIO using a presigned URL.
- Create asynchronous transcoding jobs and track Pending, Processing, Completed, and Failed states.
- Produce MP4 and adaptive HLS in up to 1080p, 720p, 480p, and 360p, without unnecessary upscaling.
- Persist originals and outputs; support adaptive playback.
- Scale workers based on Redis queue length and safely retry interrupted jobs.
- Operate entirely on self-hosted Kubernetes, without AWS services.

## Milestones

1. Local prototype: phases 1–5; upload a video and receive multiple MP4 outputs via Docker Compose.
2. Streaming MVP: phase 6; generate and play adaptive HLS.
3. Kubernetes release: phases 7–8; deploy and support multiple concurrent jobs.
4. Production readiness: phases 9–10; monitoring, security, backups, recovery, automated tests.

Future enhancements (not MVP): GPU acceleration, AV1, H.265, MPEG-DASH.
