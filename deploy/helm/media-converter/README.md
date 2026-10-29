# media-converter Helm chart

Everything the service needs in one release: the API, the Celery transcode workers, the scheduler,
the NGINX media edge, and (optionally) Postgres, Redis and MinIO.

## Install

```bash
helm upgrade --install media-converter deploy/helm/media-converter \
  --namespace media-converter --create-namespace \
  --set images.registry=registry.example.com \
  --set images.app.tag=0.1.0 \
  --set hosts.api=api.example.com \
  --set hosts.media=media.example.com \
  --set hosts.s3=s3.example.com \
  --values my-values.yaml
```

Render without installing:

```bash
helm template media-converter deploy/helm/media-converter -n media-converter
```

## Credentials

`secrets.create=true` renders a Secret from the `secrets.*` values, which default to `CHANGE_ME`
placeholders. For anything real, manage the Secret outside the release:

```bash
scripts/generate-secrets.sh | kubectl apply -n media-converter -f -
helm upgrade --install media-converter deploy/helm/media-converter -n media-converter \
  --set secrets.create=false --set secrets.existingSecret=media-converter-secrets
```

The same applies to the off-cluster backup target: `backups.offsite.existingSecret` takes an
externally managed Secret with the `RCLONE_CONFIG_OFFSITE_*` and `OFFSITE_BUCKET` keys.

## Values worth knowing

| Value | Default | Notes |
| --- | --- | --- |
| `hosts.api` / `hosts.media` / `hosts.s3` | `*.example.com` | Drive the Ingresses, `MINIO_SERVER_URL`, `MINIO_PUBLIC_ENDPOINT` and `PUBLIC_MEDIA_BASE_URL`. |
| `images.registry` | `registry.example.com` | Prefix for the three images built from this repo. Empty means bare names. |
| `images.app.tag` | `.Chart.AppVersion` | Bump this (or `--set`) to roll out a new build. |
| `config` | see `values.yaml` | Rendered verbatim into the ConfigMap; extra keys reach every app pod as env vars. |
| `postgres.enabled` / `redis.enabled` / `minio.enabled` | `true` | Turn off to use managed services and fill in `externalEndpoints.*`. |
| `keda.enabled` | `true` | Needs the KEDA CRDs (`scripts/install-keda.sh`). When on, the chart leaves `replicas` off the worker Deployment so the ScaledObject owns it. |
| `api.autoscaling.enabled` | `true` | Same deal: when on, the HPA owns the API replica count. |
| `monitoring.enabled` | `false` | ServiceMonitors + PodMonitor. Needs the Prometheus Operator CRDs. |
| `backups.enabled` | `true` | Nightly Postgres dump and MinIO replication, both into `backups.offsite`. |
| `namespace.create` | `false` | Helm normally owns the namespace via `--create-namespace`. Set `true` to have the chart create it with the Pod Security Admission labels instead. |
| `nameOverride` | `""` | Prefixes every object name, for running two releases in one namespace. Empty keeps bare names (`api`, `postgres`, …) so in-cluster DNS matches the pre-Helm manifests. |

## Alert rules and dashboard

The PrometheusRule and the Grafana dashboard are not templated — they live in `deploy/monitoring`
and are applied separately:

```bash
kubectl apply -k deploy/monitoring
```
