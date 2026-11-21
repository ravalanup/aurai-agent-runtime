{{- define "aurai-agents.labels" -}}
app.kubernetes.io/part-of: aurai
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
aurai.io/environment: {{ .Values.global.environment | quote }}
{{- end -}}

{{/* The pod selector of one component: runtime or approvals. */}}
{{- define "aurai-agents.selector" -}}
app.kubernetes.io/name: aurai-agents
app.kubernetes.io/instance: {{ .root.Release.Name }}
aurai.io/component: {{ .name }}
{{- end -}}

{{- define "aurai-agents.image" -}}
{{ required "image.repository must be set" .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}

{{- define "aurai-agents.securityContext" -}}
runAsUser: 10001
runAsNonRoot: true
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Settings both services share. */}}
{{- define "aurai-agents.commonEnv" -}}
- name: AURAI_CONFIG_DIR
  value: /config
- name: AURAI_ISSUER
  value: {{ .Values.config.oidcIssuer | quote }}
- name: AURAI_JWKS_URL
  value: {{ .Values.config.oidcJwksUrl | default (printf "%s/protocol/openid-connect/certs" (.Values.config.oidcIssuer | trimSuffix "/")) | quote }}
- name: AURAI_TOKEN_URL
  value: {{ .Values.config.tokenUrl | default (printf "%s/protocol/openid-connect/token" (.Values.config.oidcIssuer | trimSuffix "/")) | quote }}
- name: AURAI_AGENTS_AUDIENCE
  value: {{ .Values.config.agentsAudience | quote }}
- name: AURAI_APPROVALS_AUDIENCE
  value: {{ .Values.config.approvalsAudience | quote }}
- name: AURAI_APPROVAL_SERVICE_URL
  value: http://approvals.{{ .Release.Namespace }}.svc.cluster.local:{{ .Values.service.port }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.config.otlpEndpoint | quote }}
- name: AURAI_APPROVAL_SIGNING_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.name }}
      key: {{ .Values.secrets.approvalSigningKey }}
- name: AURAI_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.name }}
      key: {{ .Values.secrets.databaseUrl }}
{{- end -}}
