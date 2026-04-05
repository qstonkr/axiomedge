{{/*
Common labels
*/}}
{{- define "knowledge-local.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/*
Full name helper
*/}}
{{- define "knowledge-local.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Namespace
*/}}
{{- define "knowledge-local.namespace" -}}
{{- .Values.namespace | default "knowledge" }}
{{- end }}

{{/*
Database URL constructed from postgres values
*/}}
{{- define "knowledge-local.databaseUrl" -}}
postgresql+asyncpg://{{ .Values.postgres.user }}:{{ .Values.postgres.password }}@{{ .Values.postgres.host }}:{{ .Values.postgres.port }}/{{ .Values.postgres.database }}
{{- end }}
