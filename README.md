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
- **Web app** for PCs and phones on the media edge (React + shadcn/ui): list uploaded videos with their
  status and thumbnail, group them into folders, and upload by file picker, camera or drag and drop with upload and transcoding
  progress, then a link to the player.
- **10-foot UI**, the default layout on every device: a lean-back library and full screen player driven by the D-pad
  and media keys of a remote. **Exit TV mode** or `?tv=0` switches to the layout for uploading and organizing folders,
  and **TV mode** in its sidebar or `?tv=1` switches back. The browser remembers the choice.
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

## Upload from a PC or phone

Open `http://localhost:8080`, enter the API key (`dev-key`), go to **Upload video**, choose a video and
press **Upload and transcode**. Pick a folder before uploading to file the video there. **Uploaded videos** lists what you have uploaded; create folders and move videos between them there. The key is kept in that browser's local storage.

A phone cannot reach `localhost`, so point the stack at this machine's LAN IP first. The phone must be
on the same network, and ports 8000, 8080 and 9000 must be reachable:

```bash
PUBLIC_HOST=192.168.1.20 docker compose up -d
```

Then open `http://192.168.1.20:8080` on the phone. Keep the page open with the screen on
until the upload finishes: an interrupted upload starts again from zero. This setup sends the API key
over plain HTTP, so use it only on a trusted network; the Helm chart serves everything over HTTPS.

## Use your own domains

To reach the Compose stack through your own domains, point three hostnames at this machine and put a
reverse proxy or tunnel in front. Each hostname forwards to one local port:

| Example domain | Local port | Serves |
| --- | --- | --- |
| `media.example.com` | `8080` | Web app and transcoded media |
| `api.example.com` | `8000` | API |
| `s3.example.com` | `9000` | Presigned uploads to MinIO |

Add the public addresses to `.env`, and replace the development credentials: anyone who can reach these
domains could otherwise use `dev-key` and `minioadmin`.

```bash
PUBLIC_API_BASE_URL=https://api.example.com
PUBLIC_MEDIA_BASE_URL=https://media.example.com/media
MINIO_PUBLIC_ENDPOINT=s3.example.com
MINIO_PUBLIC_SECURE=true
API_KEYS=admin:a-long-random-key:admin
MINIO_ROOT_USER=a-random-user
MINIO_ROOT_PASSWORD=a-long-random-password
BIND_ADDRESS=127.0.0.1
```

`BIND_ADDRESS=127.0.0.1` keeps the ports off the network, so use it only when the proxy runs on this
machine. Run `docker compose up -d` to apply the changes, then open `https://media.example.com`.

The proxy must keep the original `Host` header on `s3.example.com`, because it is part of the upload
signature. It must also allow request bodies as large as your uploads. Caddy does both by default and
gets the certificates itself:

```caddyfile
media.example.com {
	reverse_proxy localhost:8080
}
api.example.com {
	reverse_proxy localhost:8000
}
s3.example.com {
	reverse_proxy localhost:9000
}
```

Providers that proxy traffic can cap request size. Cloudflare's Free and Pro plans, for example, reject
bodies over 100 MB, which blocks larger uploads through `s3.example.com`.

### Let's Encrypt certificates for another proxy

Caddy needs none of this. To use NGINX instead, install it on the server and set the addresses in `.env` as
above. `scripts/letsencrypt.sh` then requests one certificate covering all the domains, using certbot in
Docker, and configures NGINX to use it. Run it as root, once DNS points at the server and port 80 is reachable
from the internet. Running it accepts the Let's Encrypt subscriber agreement.

Try `--staging` first, because repeated failures against production hit a rate limit:

```bash
sudo scripts/letsencrypt.sh issue --email you@example.com --nginx --staging
```

Browsers do not trust staging certificates, so expect a warning. If the domains respond, get the real one:

```bash
sudo scripts/letsencrypt.sh issue --email you@example.com --nginx
```

The certificate is written to `/etc/letsencrypt/live/media-converter/`, or `media-converter-staging/` for
staging, so a staging certificate never replaces a production one. The site is written to
`/etc/nginx/conf.d/media-converter.conf`, and meets the `Host` and body size requirements above. If `nginx -t`
rejects it, the previous config is put back. NGINX stops while certbot runs. If it is not managed by systemd,
set `PROXY_STOP_CMD` and `PROXY_START_CMD` to the commands that stop and start it.

Without `--nginx`, the script only gets the certificate, for any other proxy. It then also accepts the domains
as `-d media.example.com -d api.example.com -d s3.example.com`.

Certificates last 90 days, so renew them from root's crontab. The script does nothing until 30 days remain,
then stops NGINX for a few seconds and starts it again with the new certificate.

```cron
0 4 * * * /path/to/media-converter/scripts/letsencrypt.sh renew
```

### Run NGINX in Compose

The `tls` profile adds a `proxy` service, so NGINX does not have to be installed on the server. It publishes
ports 80 and 443 and forwards each domain to its service. Get the certificate first, without `--nginx`, because
`proxy` will not start until the certificate exists. Then add this to `.env`, next to the settings above:

```bash
COMPOSE_PROFILES=tls
MEDIA_DOMAIN=media.example.com
API_DOMAIN=api.example.com
S3_DOMAIN=s3.example.com
```

Run `docker compose up -d` to start it. The certificate is mounted from `/etc/letsencrypt`, read-only, rather
than copied, which keeps the private key out of the image and lets renewed certificates reach the container.
Set `LETSENCRYPT_DIR` if it is somewhere else.

To renew, `proxy` has to release port 80 and then start again to load the new certificate:

```cron
0 4 * * * PROXY_STOP_CMD="cd /path/to/media-converter && docker compose stop proxy" PROXY_START_CMD="cd /path/to/media-converter && docker compose start proxy" /path/to/media-converter/scripts/letsencrypt.sh renew
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
| `POST` | `/v1/videos` | Register a video (optional `folder_id`), returns presigned upload (POST form + PUT URL) |
| `POST` | `/v1/videos/{id}/complete` | Verify object, FFprobe, store metadata (`ready` / `invalid`) |
| `GET` | `/v1/videos`, `/v1/videos/{id}` | List (filter `status`, `folder_id` or `none`, paginate) / detail with source URL |
| `PATCH` | `/v1/videos/{id}` | Move to a folder (`folder_id`, `null` to take it out) |
| `DELETE` | `/v1/videos/{id}` | Delete video, jobs, original and all outputs (409 while processing) |
| `POST`, `GET` | `/v1/folders` | Create (`name`) / list folders |
| `DELETE` | `/v1/folders/{id}` | Delete a folder; its videos stay, outside any folder |
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
deploy/helm/     Helm chart: config, Postgres, Redis, MinIO, API, workers, NGINX, Ingress, KEDA, backups, monitors
deploy/monitoring/  Prometheus/Grafana for Compose + PrometheusRule and dashboard for Kubernetes
deploy/nginx/   Media edge image (MIME types, CORS, range caching) serving web/
web/            React + shadcn/ui app and the HLS player page
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
