#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    printf 'error: %s is required.\n' "$name" >&2
    exit 1
  fi
}

require_secret_version() {
  local name="$1"
  require_env "$name"
  if [[ ! "${!name}" =~ ^[1-9][0-9]*$ ]]; then
    printf 'error: %s must be an explicit numeric secret version.\n' "$name" >&2
    exit 1
  fi
}

require_no_separator() {
  local name="$1"
  if [[ "${!name}" == *"|"* ]]; then
    printf 'error: %s must not contain the reserved | separator.\n' "$name" >&2
    exit 1
  fi
}

command -v gcloud >/dev/null 2>&1 || {
  printf 'error: gcloud is required.\n' >&2
  exit 1
}

CLOUD_RUN_SERVICE="${CLOUD_RUN_SERVICE:-buglens-api}"
CLOUD_RUN_CPU="${CLOUD_RUN_CPU:-2}"
CLOUD_RUN_MEMORY="${CLOUD_RUN_MEMORY:-2Gi}"
CLOUD_RUN_CONCURRENCY="${CLOUD_RUN_CONCURRENCY:-4}"
CLOUD_RUN_MIN_INSTANCES="${CLOUD_RUN_MIN_INSTANCES:-0}"
CLOUD_RUN_MAX_INSTANCES="${CLOUD_RUN_MAX_INSTANCES:-5}"
CLOUD_RUN_TIMEOUT="${CLOUD_RUN_TIMEOUT:-900}"

required=(
  GCP_PROJECT_ID
  GCP_REGION
  API_IMAGE_URI
  RUNTIME_SERVICE_ACCOUNT
  CLOUD_SQL_INSTANCE
  GCS_BUCKET
  APP_BASE_URL
  GITHUB_APP_ID
  GITHUB_APP_SLUG
  DATABASE_URL_SECRET
  SESSION_SECRET_SECRET
  GITHUB_PRIVATE_KEY_SECRET
  GITHUB_CLIENT_ID_SECRET
  GITHUB_CLIENT_SECRET_SECRET
)

for name in "${required[@]}"; do
  require_env "$name"
done

GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-$GCP_PROJECT_ID}"
GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-global}"

if [[ ! "$API_IMAGE_URI" =~ :[0-9a-fA-F]{40}$ ]]; then
  printf 'error: API_IMAGE_URI must use a full Git SHA tag.\n' >&2
  exit 1
fi

secret_versions=(
  DATABASE_URL_SECRET_VERSION
  SESSION_SECRET_SECRET_VERSION
  GITHUB_PRIVATE_KEY_SECRET_VERSION
  GITHUB_CLIENT_ID_SECRET_VERSION
  GITHUB_CLIENT_SECRET_SECRET_VERSION
)

for name in "${secret_versions[@]}"; do
  require_secret_version "$name"
done

env_values=(
  APP_BASE_URL
  GITHUB_APP_ID
  GITHUB_APP_SLUG
  GCS_BUCKET
)

for name in "${env_values[@]}"; do
  require_no_separator "$name"
done

require_no_separator GOOGLE_CLOUD_PROJECT
require_no_separator GOOGLE_CLOUD_LOCATION

if [[ "$APP_BASE_URL" != https://* ]]; then
  printf 'error: APP_BASE_URL must start with https://.\n' >&2
  exit 1
fi

if [[ "$APP_BASE_URL" == */ ]]; then
  printf 'error: APP_BASE_URL must not have a trailing slash.\n' >&2
  exit 1
fi

github_private_key_path="/var/secrets/buglens/github-private-key.pem"
frontend_base_url="$APP_BASE_URL"
backend_base_url="${APP_BASE_URL}/api"
github_callback_url="${APP_BASE_URL}/api/github/oauth/callback"
environment_variables="^|^FRONTEND_BASE_URL=${frontend_base_url}"
environment_variables+="|BACKEND_BASE_URL=${backend_base_url}"
environment_variables+="|GITHUB_CALLBACK_URL=${github_callback_url}"
environment_variables+="|GITHUB_APP_ID=${GITHUB_APP_ID}"
environment_variables+="|GITHUB_APP_SLUG=${GITHUB_APP_SLUG}"
environment_variables+="|GITHUB_PRIVATE_KEY_PATH=${github_private_key_path}"
environment_variables+="|LOG_LEVEL=INFO|LOG_FORMAT=json"
environment_variables+="|EVIDENCE_STORAGE_BACKEND=gcs|GCS_BUCKET=${GCS_BUCKET}"
environment_variables+="|GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT}"
environment_variables+="|GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION}"
environment_variables+="|DATABASE_POOL_SIZE=5|DATABASE_MAX_OVERFLOW=2"
environment_variables+="|DATABASE_POOL_TIMEOUT_SECONDS=30"
environment_variables+="|DATABASE_POOL_RECYCLE_SECONDS=1800"
environment_variables+="|SESSION_COOKIE_SECURE=true"
environment_variables+="|PLAYWRIGHT_ALLOW_PRIVATE_NETWORK=false"

secret_references="DATABASE_URL=${DATABASE_URL_SECRET}:${DATABASE_URL_SECRET_VERSION}"
secret_references+=",SESSION_SECRET=${SESSION_SECRET_SECRET}:${SESSION_SECRET_SECRET_VERSION}"
secret_references+=",GITHUB_CLIENT_ID=${GITHUB_CLIENT_ID_SECRET}:${GITHUB_CLIENT_ID_SECRET_VERSION}"
secret_references+=",GITHUB_CLIENT_SECRET=${GITHUB_CLIENT_SECRET_SECRET}:${GITHUB_CLIENT_SECRET_SECRET_VERSION}"
secret_references+=",${github_private_key_path}=${GITHUB_PRIVATE_KEY_SECRET}:${GITHUB_PRIVATE_KEY_SECRET_VERSION}"

gcloud run deploy "$CLOUD_RUN_SERVICE" \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --image="$API_IMAGE_URI" \
  --service-account="$RUNTIME_SERVICE_ACCOUNT" \
  --set-cloudsql-instances="$CLOUD_SQL_INSTANCE" \
  --allow-unauthenticated \
  --ingress=internal-and-cloud-load-balancing \
  --no-default-url \
  --execution-environment=gen2 \
  --cpu="$CLOUD_RUN_CPU" \
  --memory="$CLOUD_RUN_MEMORY" \
  --concurrency="$CLOUD_RUN_CONCURRENCY" \
  --min-instances="$CLOUD_RUN_MIN_INSTANCES" \
  --max-instances="$CLOUD_RUN_MAX_INSTANCES" \
  --timeout="$CLOUD_RUN_TIMEOUT" \
  --set-env-vars="$environment_variables" \
  --set-secrets="$secret_references" \
  --startup-probe="httpGet.path=/health,timeoutSeconds=3,periodSeconds=5,failureThreshold=12" \
  --liveness-probe="httpGet.path=/health,timeoutSeconds=3,periodSeconds=30,failureThreshold=3" \
  --readiness-probe="httpGet.path=/ready,timeoutSeconds=3,periodSeconds=10,failureThreshold=3,successThreshold=1" \
  --quiet
