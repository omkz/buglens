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

required=(
  GCP_PROJECT_ID
  GCP_REGION
  IMAGE_URI
  MIGRATION_SERVICE_ACCOUNT
  CLOUD_SQL_INSTANCE
  DATABASE_URL_SECRET
  DATABASE_URL_SECRET_VERSION
)

for name in "${required[@]}"; do
  require_env "$name"
done

if [[ ! "$DATABASE_URL_SECRET_VERSION" =~ ^[1-9][0-9]*$ ]]; then
  printf 'error: DATABASE_URL_SECRET_VERSION must be an explicit numeric secret version.\n' >&2
  exit 1
fi

gcloud run jobs deploy "$MIGRATION_JOB_NAME" \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --image="$IMAGE_URI" \
  --service-account="$MIGRATION_SERVICE_ACCOUNT" \
  --set-cloudsql-instances="$CLOUD_SQL_INSTANCE" \
  --set-secrets="DATABASE_URL=${DATABASE_URL_SECRET}:${DATABASE_URL_SECRET_VERSION}" \
  --command=uv \
  --args=run,--no-dev,alembic,upgrade,head \
  --tasks=1 \
  --parallelism=1 \
  --max-retries=0 \
  --task-timeout=10m \
  --quiet
