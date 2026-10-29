{{/*
Component names are bare by default (api, postgres, transcode-worker, ...) so in-cluster DNS matches
what the raw manifests used. Set nameOverride (or fullnameOverride) to run two releases side by side
in one namespace; every reference below is templated, so names stay consistent either way.
*/}}
{{- define "mc.prefix" -}}
{{- if .Values.fullnameOverride -}}
{{ .Values.fullnameOverride }}-
{{- else if .Values.nameOverride -}}
{{ .Values.nameOverride }}-
{{- end -}}
{{- end -}}

{{/* usage: include "mc.name" (list $ "api") */}}
{{- define "mc.name" -}}
{{- $root := index . 0 -}}
{{- $component := index . 1 -}}
{{- printf "%s%s" (include "mc.prefix" $root) $component -}}
{{- end -}}

{{- define "mc.namespace" -}}
{{- default .Release.Namespace .Values.namespace.name -}}
{{- end -}}

{{- define "mc.configMapName" -}}
{{ include "mc.name" (list . "media-converter-config") }}
{{- end -}}

{{- define "mc.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{ .Values.secrets.existingSecret }}
{{- else -}}
{{ include "mc.name" (list . "media-converter-secrets") }}
{{- end -}}
{{- end -}}

{{- define "mc.backupSecretName" -}}
{{- if .Values.backups.offsite.existingSecret -}}
{{ .Values.backups.offsite.existingSecret }}
{{- else -}}
{{ include "mc.name" (list . "backup-remote") }}
{{- end -}}
{{- end -}}

{{/* Labels shared by every object. */}}
{{- define "mc.labels" -}}
app.kubernetes.io/name: {{ default .Chart.Name .Values.nameOverride }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: media-converter
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/*
Selector label. Kept as the single `app: <component>` key the raw manifests used, so the monitoring
add-on (deploy/monitoring) and anything else selecting on it keeps matching.
usage: include "mc.selectorLabels" (list $ "api")
*/}}
{{- define "mc.selectorLabels" -}}
{{- $root := index . 0 -}}
{{- $component := index . 1 -}}
app: {{ include "mc.name" (list $root $component) }}
{{- end -}}

{{/* Image references. */}}
{{- define "mc.image.app" -}}
{{- $r := .Values.images.registry -}}
{{- $tag := default .Chart.AppVersion .Values.images.app.tag -}}
{{- if $r }}{{ printf "%s/%s:%s" $r .Values.images.app.repository $tag }}{{ else }}{{ printf "%s:%s" .Values.images.app.repository $tag }}{{ end -}}
{{- end -}}

{{- define "mc.image.nginx" -}}
{{- $r := .Values.images.registry -}}
{{- $tag := default .Chart.AppVersion .Values.images.nginx.tag -}}
{{- if $r }}{{ printf "%s/%s:%s" $r .Values.images.nginx.repository $tag }}{{ else }}{{ printf "%s:%s" .Values.images.nginx.repository $tag }}{{ end -}}
{{- end -}}

{{- define "mc.image.minio" -}}
{{- $r := .Values.images.registry -}}
{{- if $r }}{{ printf "%s/%s:%s" $r .Values.images.minio.repository .Values.images.minio.tag }}{{ else }}{{ printf "%s:%s" .Values.images.minio.repository .Values.images.minio.tag }}{{ end -}}
{{- end -}}

{{- define "mc.imagePullSecrets" -}}
{{- with .Values.images.pullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end -}}

{{/* In-cluster endpoints. Fall back to externalEndpoints when a dependency is not deployed here. */}}
{{- define "mc.postgresHost" -}}
{{- if .Values.postgres.enabled -}}
{{ include "mc.name" (list . "postgres") }}
{{- else -}}
{{ required "externalEndpoints.postgresHost is required when postgres.enabled=false" .Values.externalEndpoints.postgresHost }}
{{- end -}}
{{- end -}}

{{- define "mc.redisUrl" -}}
{{- if .Values.redis.enabled -}}
{{ printf "redis://%s:%v/0" (include "mc.name" (list . "redis")) .Values.redis.service.port }}
{{- else -}}
{{ required "externalEndpoints.redisUrl is required when redis.enabled=false" .Values.externalEndpoints.redisUrl }}
{{- end -}}
{{- end -}}

{{- define "mc.minioEndpoint" -}}
{{- if .Values.minio.enabled -}}
{{ printf "%s:%v" (include "mc.name" (list . "minio")) .Values.minio.service.apiPort }}
{{- else -}}
{{ required "externalEndpoints.minioEndpoint is required when minio.enabled=false" .Values.externalEndpoints.minioEndpoint }}
{{- end -}}
{{- end -}}

{{- define "mc.databaseUrl" -}}
{{- if .Values.secrets.databaseUrl -}}
{{ .Values.secrets.databaseUrl }}
{{- else -}}
{{ printf "postgresql+psycopg://%s:%s@%s:%v/%s" .Values.secrets.postgresUser .Values.secrets.postgresPassword (include "mc.postgresHost" .) .Values.postgres.service.port .Values.secrets.postgresDb }}
{{- end -}}
{{- end -}}

{{/* envFrom shared by the API, workers and scheduler. */}}
{{- define "mc.appEnvFrom" -}}
envFrom:
  - configMapRef: { name: {{ include "mc.configMapName" . }} }
  - secretRef: { name: {{ include "mc.secretName" . }} }
{{- end -}}
