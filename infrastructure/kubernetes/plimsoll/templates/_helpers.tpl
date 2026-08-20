{{- define "plimsoll.labels" -}}
app.kubernetes.io/name: plimsoll
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "plimsoll.env" -}}
- name: PLIMSOLL_ENVIRONMENT
  value: production
- name: PLIMSOLL_DATABASE_URL
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: databaseUrl }
- name: PLIMSOLL_REDIS_URL
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: redisUrl }
- name: PLIMSOLL_JWT_SECRET
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: jwtSecret }
- name: PLIMSOLL_CREDENTIAL_KEY
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: credentialKey }
{{- /* Present only during a key rotation. optional, so the pod starts
       without it rather than crash-looping when it is absent. */}}
- name: PLIMSOLL_CREDENTIAL_KEYS_RETIRED
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: credentialKeysRetired, optional: true }
- name: PLIMSOLL_S3_ENDPOINT
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: s3Endpoint }
- name: PLIMSOLL_S3_ACCESS_KEY
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: s3AccessKey }
- name: PLIMSOLL_S3_SECRET_KEY
  valueFrom:
    secretKeyRef: { name: {{ .Values.existingSecret }}, key: s3SecretKey }
{{- end -}}

{{- define "plimsoll.podSecurity" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  fsGroup: 10001
  seccompProfile: { type: RuntimeDefault }
{{- end -}}

{{- define "plimsoll.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: ["ALL"] }
{{- end -}}
