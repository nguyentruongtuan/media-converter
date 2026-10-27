# Operations

## Deploying to Kubernetes

Prerequisites: a cluster with a default StorageClass, ingress-nginx, cert-manager (ClusterIssuer
`letsencrypt-prod`), KEDA, and optionally kube-prometheus-stack.

1. **Images.** Build and push the three images:
   ```bash
   make push REGISTRY=registry.example.com VERSION=0.1.0
   ```
   Update `images:` in `deploy/k8s/kustomization.yaml` to match.
2. **Hostnames.** Replace `example.com` in `deploy/k8s/configmap.yaml` (`MINIO_PUBLIC_ENDPOINT`,
   `PUBLIC_MEDIA_BASE_URL`), `deploy/k8s/minio.yaml` (`MINIO_SERVER_URL`) and `deploy/k8s/ingress.yaml`.
   Three hosts: `api.` (FastAPI), `media.` (NGINX edge and player), `s3.` (presigned uploads to MinIO).
3. **Secrets.** Never apply `secrets.yaml` as is:
   ```bash
   scripts/generate-secrets.sh > /tmp/secrets.yaml   # prints the admin API key on stderr
   ```
   Seal it (Sealed Secrets / SOPS / External Secrets) and remove `secrets.yaml` from the kustomization,
   or apply it directly for a first test. Fill in `backup-remote` in `backups.yaml` too.
4. **KEDA.** `scripts/install-keda.sh`
5. **Deploy.** `kubectl apply -k deploy/k8s`, then `kubectl apply -k deploy/monitoring` if the
   Prometheus Operator is installed. If Prometheus is not at
   `prometheus-operated.monitoring.svc:9090`, edit the `prometheus` trigger in `keda.yaml`.
6. **Verify.**
   ```bash
   kubectl -n media-converter get pods
   python3 scripts/e2e.py sample.mp4 --api https://api.example.com --key <admin-key>
   ```

Database migrations run in the API pods' init container; a Postgres advisory lock serialises them.

## Components and scaling

| Component | Kind | Replicas | Notes |
| --- | --- | --- | --- |
| api | Deployment + HPA | 2–6 (CPU 70%) | One uvicorn process per pod so metrics stay in one registry |
| transcode-worker | Deployment + KEDA | 0–10 | One job per pod (`--pool=solo`), 2–4 CPU, 2–6 GiB, 50 GiB scratch |
| scheduler | Deployment | exactly 1 | Celery beat + maintenance queue (stale-job recovery) |
| media | Deployment | 2 | NGINX edge with 1 MiB slice cache |
| postgres, redis, minio | StatefulSet | 1 | PVCs 20 Gi / 5 Gi / 500 Gi |

**Worker autoscaling.** KEDA takes the higher of two triggers: Redis list length of the `transcode`
queue (works without Prometheus and handles scale from zero) and `active jobs + queued jobs` from
Prometheus (keeps busy pods alive, since Celery removes a message from the list as soon as a worker
takes it). Scale-down is limited to one pod every two minutes after a 10-minute stabilisation window.

**Concurrency limits.**
- Per pod: one transcode (`--pool=solo --concurrency=1 --prefetch-multiplier=1`).
- Cluster-wide: `MAX_CONCURRENT_JOBS` (Redis-backed slot leases). Workers that cannot get a slot
  re-queue the job after `CONCURRENCY_RETRY_SECONDS` without consuming an attempt.
- Ceiling on pods: `maxReplicaCount` in `keda.yaml`. Keep it ≥ `MAX_CONCURRENT_JOBS`.

**Scratch disk.** Each job needs roughly `source size × DISK_SPACE_FACTOR` (default 4) of scratch space
for the source, MP4 renditions and HLS segments. The worker checks free space before starting and
fails the attempt (retried) when there is not enough. Raise the `scratch` `sizeLimit` and the
`ephemeral-storage` request for long or high-bitrate sources.

## Job reliability model

- **Claim.** A worker moves a job to `processing` with a single conditional `UPDATE` that succeeds only
  if the job is `pending`, or `processing` with an expired lease. Duplicate deliveries are no-ops.
- **Lease.** A background thread renews the lease every `JOB_HEARTBEAT_SECONDS` (default 30s, lease
  300s). If renewal finds another worker owns the job, the current worker abandons it.
- **Retries.** Any failure other than a permanent one (missing source, invalid request) is retried
  with exponential backoff (`30s × 2^(attempt-1)`, max 10 min) until `JOB_MAX_ATTEMPTS` (3). Each
  attempt rewrites the job's output prefix, so partial outputs never mix.
- **Timeouts.** `JOB_TIMEOUT_SECONDS` (4h) bounds a whole attempt; FFmpeg is killed when it runs out.
- **Dead letter.** Jobs that fail permanently are marked `failed` in Postgres (source of truth) and
  pushed to the Redis list `transcode:dead_letter` (`GET /v1/admin/dead-letter`).
- **Interrupted jobs.** If a worker dies (OOM kill, node loss, `kubectl delete --force`), its lease
  expires and the scheduler's recovery task (every 60s) re-queues the job, or fails it if it was on
  its last attempt. The recovery task also re-sends `pending` jobs whose queue message was lost
  (e.g. Redis restored from an old snapshot) after `STALE_PENDING_SECONDS`.
- **Graceful shutdown.** SIGTERM starts Celery's warm shutdown and the pod has
  `terminationGracePeriodSeconds: 3600` to finish its job. If it is killed first, the steps above
  recover the job.

For very long sources, consider KEDA `ScaledJob` (one Kubernetes Job per message) so scale-down never
interrupts a running transcode.

## Security

- **Authentication.** `X-API-Key` header; keys configured in `API_KEYS` as `name:key[:admin]`.
  Keys are compared as SHA-256 digests in constant time.
- **Authorization.** Videos and jobs belong to the key's name; non-admin keys only see their own.
  Admin keys see everything and can read the dead-letter list.
- **Upload limits.** Extension and content-type allow-lists, `MAX_UPLOAD_BYTES` enforced by the
  presigned POST policy and re-checked on completion, declared size must match, FFprobe must find a
  decodable video stream within `MAX_VIDEO_DURATION_SECONDS` and `MAX_SOURCE_WIDTH/HEIGHT`.
- **Bucket policies.** `videos-source` never has an anonymous policy; originals are only reachable
  with short-lived presigned URLs. `videos-output` gets anonymous `s3:GetObject` (no listing) so NGINX
  can serve it; set `OUTPUT_BUCKET_PUBLIC_READ=false` to remove it and serve outputs another way.
  Applied by `python -m app.cli init-storage` on every API start.
- **Network.** `/metrics` is not routed by the Ingress. Containers run as non-root with read-only root
  filesystems and all capabilities dropped (except the stateful services).

## Logging

All Python services log one JSON object per line to stdout with `request_id` (API, echoed as
`X-Request-ID`) and `job_id` (worker). Ship them with any cluster log agent, for example Grafana
Loki + Promtail/Alloy or Fluent Bit → OpenSearch; no application changes are needed. Useful queries:

```text
{namespace="media-converter"} | json | job_id="<id>"
{namespace="media-converter", app="transcode-worker"} | json | level="ERROR"
```

## Monitoring and alerts

Metrics: `http_requests_total`, `http_request_duration_seconds`, `transcoder_queue_depth`,
`transcoder_jobs{status}`, `transcoder_jobs_active`, `transcoder_jobs_finished_total{outcome}`,
`transcoder_job_duration_seconds`, `transcoder_job_realtime_factor`,
`transcoder_ffmpeg_duration_seconds{step,rendition}`, `transcoder_ffmpeg_cpu_seconds_total{step}`,
`transcoder_ffmpeg_max_rss_bytes`, `transcoder_ffmpeg_failures_total`, `transcoder_dead_letter_size`,
`transcoder_uploads_rejected_total{reason}`, plus MinIO cluster metrics.

Alert rules live in `deploy/monitoring/alerts.yml`; regenerate the Kubernetes resource with
`python3 scripts/render_prometheusrule.py` after editing.

### Failed jobs
`TranscodeJobsFailing` / `TranscodeFailureRateHigh`. Inspect `GET /v1/jobs?status=failed` (the `error`
field holds the FFmpeg stderr tail) and the worker logs for the `job_id`. Typical causes: unusual
source codecs, out-of-memory kills (raise the worker memory limit), scratch disk exhaustion. After a
fix, `POST /v1/jobs/{id}/retry`.

### Dead letter
`DeadLetterGrowing`. Review `GET /v1/admin/dead-letter`, fix the cause and retry the jobs. The list is
trimmed to `DEAD_LETTER_MAX_LENGTH` entries; Postgres keeps every failed job.

### Queue backlog
`TranscodeQueueBacklog` / `TranscodeQueueStalled`. Check `kubectl get scaledobject,hpa`, KEDA operator
logs, pending worker pods (insufficient CPU/ephemeral storage), and whether `MAX_CONCURRENT_JOBS` or
`maxReplicaCount` is the bottleneck.

### Storage capacity
`MinioStorageLow`, `MinioStorageCritical`, `PersistentVolumeAlmostFull`. Expand the PVC (if the
StorageClass allows), delete unused videos via the API, or add a lifecycle rule for old outputs
(outputs can be regenerated from originals).

## Backups and restore

- `postgres-backup` CronJob (02:15 UTC): `pg_dump -Fc` to `minio:backups/postgres/` (14-day retention)
  and the off-site target.
- `object-storage-backup` CronJob (02:45 UTC): `rclone sync` of `videos-source` and `videos-output` to
  the off-site S3-compatible target configured in the `backup-remote` secret.

Restore:

```bash
# Database
kubectl -n media-converter scale deploy/api deploy/transcode-worker deploy/scheduler --replicas=0
kubectl -n media-converter exec -i postgres-0 -- pg_restore -U transcoder -d transcoder --clean --if-exists < transcoder-<ts>.dump
# Objects (from a machine with rclone configured for both ends)
rclone sync offsite:media-converter-backup/videos-source minio:videos-source
rclone sync offsite:media-converter-backup/videos-output minio:videos-output
kubectl -n media-converter scale deploy/api --replicas=2; kubectl -n media-converter scale deploy/scheduler --replicas=1
```

KEDA restores worker replicas on its own. Jobs left `processing` in the restored database are
re-queued by the recovery task once their leases expire.

Test the restore procedure on a scratch namespace at least quarterly.

## Release checklist

1. CI green on `main` (lint, unit, FFmpeg integration, manifest validation, Compose e2e + chaos).
2. Tag `vX.Y.Z`; the release job pushes images to GHCR.
3. Bump image tags in `deploy/k8s/kustomization.yaml`, `kubectl apply -k deploy/k8s`.
4. Run `scripts/e2e.py` against production and play the result on iOS Safari, Android Chrome and a
   desktop browser (see docs/testing.md).
5. Watch the Grafana dashboard and alerts for an hour.
