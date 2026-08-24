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

require_env GCP_PROJECT_ID
require_env GCP_REGION

ARTIFACT_REPOSITORY="${ARTIFACT_REPOSITORY:-buglens}"
GIT_SHA="${GIT_SHA:-$(git rev-parse HEAD)}"

if [[ ! "$GIT_SHA" =~ ^[0-9a-fA-F]{40}$ ]]; then
  printf 'error: GIT_SHA must be a full 40-character hexadecimal Git SHA.\n' >&2
  exit 1
fi

registry_host="${GCP_REGION}-docker.pkg.dev"
API_IMAGE_URI="${registry_host}/${GCP_PROJECT_ID}/${ARTIFACT_REPOSITORY}/buglens-api:${GIT_SHA}"
WEB_IMAGE_URI="${registry_host}/${GCP_PROJECT_ID}/${ARTIFACT_REPOSITORY}/buglens-web:${GIT_SHA}"

gcloud builds submit apps/api \
  --project="$GCP_PROJECT_ID" \
  --tag="$API_IMAGE_URI" \
  --quiet

gcloud builds submit apps/web \
  --project="$GCP_PROJECT_ID" \
  --tag="$WEB_IMAGE_URI" \
  --quiet

printf 'API_IMAGE_URI=%s\n' "$API_IMAGE_URI"
printf 'WEB_IMAGE_URI=%s\n' "$WEB_IMAGE_URI"
