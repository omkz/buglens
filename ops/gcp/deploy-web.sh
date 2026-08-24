#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    printf 'error: %s is required.\n' "$name" >&2
    exit 1
  fi
}

command -v gcloud >/dev/null 2>&1 || {
  printf 'error: gcloud is required.\n' >&2
  exit 1
}

WEB_CLOUD_RUN_SERVICE="${WEB_CLOUD_RUN_SERVICE:-buglens-web}"
WEB_CLOUD_RUN_CPU="${WEB_CLOUD_RUN_CPU:-1}"
WEB_CLOUD_RUN_MEMORY="${WEB_CLOUD_RUN_MEMORY:-512Mi}"
WEB_CLOUD_RUN_CONCURRENCY="${WEB_CLOUD_RUN_CONCURRENCY:-80}"
WEB_CLOUD_RUN_MIN_INSTANCES="${WEB_CLOUD_RUN_MIN_INSTANCES:-0}"
WEB_CLOUD_RUN_MAX_INSTANCES="${WEB_CLOUD_RUN_MAX_INSTANCES:-5}"
WEB_CLOUD_RUN_TIMEOUT="${WEB_CLOUD_RUN_TIMEOUT:-300}"

required=(
  GCP_PROJECT_ID
  GCP_REGION
  WEB_IMAGE_URI
  WEB_RUNTIME_SERVICE_ACCOUNT
)

for name in "${required[@]}"; do
  require_env "$name"
done

if [[ ! "$WEB_IMAGE_URI" =~ :[0-9a-fA-F]{40}$ ]]; then
  printf 'error: WEB_IMAGE_URI must use a full Git SHA tag.\n' >&2
  exit 1
fi

gcloud run deploy "$WEB_CLOUD_RUN_SERVICE" \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --image="$WEB_IMAGE_URI" \
  --service-account="$WEB_RUNTIME_SERVICE_ACCOUNT" \
  --allow-unauthenticated \
  --ingress=internal-and-cloud-load-balancing \
  --no-default-url \
  --execution-environment=gen2 \
  --cpu="$WEB_CLOUD_RUN_CPU" \
  --memory="$WEB_CLOUD_RUN_MEMORY" \
  --concurrency="$WEB_CLOUD_RUN_CONCURRENCY" \
  --min-instances="$WEB_CLOUD_RUN_MIN_INSTANCES" \
  --max-instances="$WEB_CLOUD_RUN_MAX_INSTANCES" \
  --timeout="$WEB_CLOUD_RUN_TIMEOUT" \
  --startup-probe="httpGet.path=/health,timeoutSeconds=3,periodSeconds=5,failureThreshold=12" \
  --liveness-probe="httpGet.path=/health,timeoutSeconds=3,periodSeconds=30,failureThreshold=3" \
  --quiet
