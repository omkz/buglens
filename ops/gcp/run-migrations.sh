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

MIGRATION_JOB_NAME="${MIGRATION_JOB_NAME:-buglens-migrate}"

require_env GCP_PROJECT_ID
require_env GCP_REGION

gcloud run jobs execute "$MIGRATION_JOB_NAME" \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --wait \
  --quiet
