# Media Converter

A self-hosted, AWS MediaConvert-like video transcoding platform that runs entirely on Kubernetes with
FastAPI, Celery, Redis, PostgreSQL, MinIO, FFmpeg, NGINX and KEDA. No AWS services.

```text
Client ──► FastAPI ──► PostgreSQL (metadata, job state)
   │          ├──────► MinIO  (presigned URLs, original videos)
   │          └──────► Redis  (Celery queue) ──► Celery workers ──► FFmpeg ──► MinIO (outputs)
   │                                                 ▲                              │
   │                                    KEDA scales on queue + in-flight jobs       ▼
   └──── direct upload to MinIO (presigned POST/PUT)          NGINX ──► HLS / MP4 player
```

## Features

- **Direct uploads** to MinIO with presigned POST (size enforced by the policy) or PUT.
- **Validation** on upload completion: object exists, size matches, FFprobe confirms a real video
  stream, duration and resolution limits. Corrupt uploads are marked `invalid` and deleted.
- **Transcoding** to H.264 (High/Main) + AAC-LC MP4 at 1080p/720p/480p/360p, preserving the display
  aspect ratio (rotation and anamorphic SAR handled), **never upscaling**, capped-CRF quality with
  configurable CRF and x264 preset, 2-second aligned keyframes, `+faststart`, plus a JPEG thumbnail.
- **Adaptive HLS**: per-rendition VOD playlists and segments plus a `master.m3u8` with
  `BANDWIDTH`, `AVERAGE-BANDWIDTH`, `RESOLUTION`, `FRAME-RATE` and `CODECS`.
- **Jobs**: `pending → processing → completed | failed`, progress %, idempotency keys, atomic
  claims with leases and heartbeats, exponential-backoff retries, dead-letter list, automatic
  recovery of jobs whose worker died, cluster-wide concurrency cap.
- **Kubernetes**: Kustomize manifests for every component, probes, resource limits, scratch volumes,
  Ingress + cert-manager TLS, KEDA autoscaling (including scale-to-zero), backups.
- **Operations**: Prometheus metrics (API, queue, jobs, FFmpeg CPU/memory/time), Grafana dashboard,
  alert rules, structured JSON logs with request/job IDs, API-key auth with per-owner isolation.

## Quick start (Docker Compose)

```bash
docker compose up -d --build
```

The first build compiles MinIO from source (MinIO no longer publishes pullable images; set
`MINIO_IMAGE` in `.env` to use a mirror). Services: API `http://localhost:8000` (docs at `/docs`),
media edge + player `http://localhost:8080` (`MEDIA_PORT` in `.env` if 8080 is taken), MinIO console
`http://localhost:9001` (minioadmin/minioadmin). Dev API key: `dev-key`.

Run the end-to-end smoke test (generates a 10s 1080p sample with FFmpeg inside the worker):

```bash
make sample e2e
```

Monitoring (Prometheus `:9090`, Grafana `:3000`):

```bash
docker compose --profile monitoring up -d
```

## API walkthrough

```bash
API=http://localhost:8000; KEY=dev-key

# 1. Register the upload and get a presigned URL
curl -s -X POST $API/v1/videos -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"filename":"movie.mp4","content_type":"video/mp4","size_bytes":'$(stat -f%z movie.mp4)'}' > video.json

# 2. Upload straight to MinIO (presigned PUT; the POST form in "upload.fields" also works)
curl -s -T movie.mp4 -H 'Content-Type: video/mp4' "$(jq -r .upload.put_url video.json)"

# 3. Validate the upload (FFprobe) and store metadata
VID=$(jq -r .video.id video.json)
curl -s -X POST $API/v1/videos/$VID/complete -H "X-API-Key: $KEY"

# 4. Create a transcoding job (idempotent with Idempotency-Key)
curl -s -X POST $API/v1/videos/$VID/jobs -H "X-API-Key: $KEY" -H 'Idempotency-Key: movie-v1' \
  -H 'Content-Type: application/json' -d '{"presets":["1080p","720p","480p","360p"],"formats":["mp4","hls"],"crf":23}'

# 5. Poll; when completed, hls_url points at master.m3u8 behind NGINX
curl -s $API/v1/jobs/<job-id> -H "X-API-Key: $KEY" | jq '{status, progress, hls_url}'
```

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/v1/videos` | Register a video, returns presigned upload (POST form + PUT URL) |
| `POST` | `/v1/videos/{id}/complete` | Verify object, FFprobe, store metadata (`ready` / `invalid`) |
| `GET` | `/v1/videos`, `/v1/videos/{id}` | List (filter `status`, paginate) / detail with source URL |
| `DELETE` | `/v1/videos/{id}` | Delete video, jobs, original and all outputs (409 while processing) |
| `POST` | `/v1/videos/{id}/jobs` | Create a job (`presets`, `formats`, `crf`, `x264_preset`) |
| `GET` | `/v1/jobs`, `/v1/jobs/{id}` | List (filter `video_id`, `status`) / detail with output URLs |
| `POST` | `/v1/jobs/{id}/retry` | Re-run a failed job |
| `GET` | `/v1/admin/dead-letter` | Dead-letter entries (admin key) |
| `GET` | `/health`, `/health/live`, `/health/ready` | Liveness / readiness (DB, Redis, MinIO) |
| `GET` | `/metrics` | Prometheus metrics (not exposed by the Ingress) |

Errors share one shape: `{"error": {"code", "message", "request_id"}}`.

## Output layout

```text
videos-output/<video-id>/<job-id>/
  mp4/{1080p,720p,480p,360p}.mp4
  hls/master.m3u8
  hls/<rendition>/index.m3u8, segment_00000.ts …
  thumbnail.jpg
```

## Project layout

```text
app/            FastAPI app, Celery worker, services (ffprobe, ffmpeg, hls, transcoder, jobs, storage)
alembic/        Database migrations
tests/          Unit, API and FFmpeg integration tests; tests/load (Locust)
deploy/k8s/     Kustomize base: namespace, config, Postgres, Redis, MinIO, API, workers, NGINX, Ingress, KEDA, backups
deploy/monitoring/  Prometheus/Grafana for Compose + ServiceMonitors, PrometheusRule, dashboard for Kubernetes
deploy/nginx/   Media edge image (MIME types, CORS, range caching) + player/
deploy/minio/   MinIO built from source
scripts/        e2e smoke test, chaos/recovery test, KEDA install, secret generation
docs/           Operations runbook and test plan
```

## Development

```bash
make install test      # unit + API tests (FFmpeg tests skip without ffmpeg)
make test-docker       # full suite in the image, including real FFmpeg transcodes
make lint
```

See [docs/operations.md](docs/operations.md) for deployment and runbooks and
[docs/testing.md](docs/testing.md) for the test plan and device compatibility.
