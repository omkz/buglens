#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../.." && pwd)"
test_directory="$(mktemp -d)"
trap 'rm -rf "$test_directory"' EXIT
gcloud_log="${test_directory}/gcloud.log"

cat >"${test_directory}/gcloud" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$GCLOUD_LOG"
EOF
chmod +x "${test_directory}/gcloud"

assert_contains() {
  local file="$1"
  local expected="$2"
  if ! grep -Fq -- "$expected" "$file"; then
    printf 'error: expected %s to contain %s.\n' "$file" "$expected" >&2
    exit 1
  fi
}

sha="0123456789abcdef0123456789abcdef01234567"
api_image="us-central1-docker.pkg.dev/test-project/buglens/buglens-api:${sha}"
web_image="us-central1-docker.pkg.dev/test-project/buglens/buglens-web:${sha}"
common_path="${test_directory}:${PATH}"

: >"$gcloud_log"
build_output="$(cd "$repository_root" && env \
  PATH="$common_path" \
  GCLOUD_LOG="$gcloud_log" \
  GCP_PROJECT_ID=test-project \
  GCP_REGION=us-central1 \
  GIT_SHA="$sha" \
  ./ops/gcp/build-images.sh)"
[[ "$build_output" == "API_IMAGE_URI=${api_image}"$'\n'"WEB_IMAGE_URI=${web_image}" ]] || {
  printf 'error: build-images.sh emitted unexpected image references.\n' >&2
  exit 1
}
[[ "$build_output" != *':latest'* ]] || {
  printf 'error: build-images.sh must not emit latest tags.\n' >&2
  exit 1
}
assert_contains "$gcloud_log" "builds submit apps/api --project=test-project --tag=${api_image} --quiet"
assert_contains "$gcloud_log" "builds submit apps/web --project=test-project --tag=${web_image} --quiet"

if (cd "$repository_root" && env \
  PATH="$common_path" \
  GCLOUD_LOG="$gcloud_log" \
  GCP_PROJECT_ID=test-project \
  GCP_REGION=us-central1 \
  GIT_SHA=short \
  ./ops/gcp/build-images.sh >/dev/null 2>&1); then
  printf 'error: build-images.sh accepted a non-full Git SHA.\n' >&2
  exit 1
fi

: >"$gcloud_log"
env \
  PATH="$common_path" \
  GCLOUD_LOG="$gcloud_log" \
  GCP_PROJECT_ID=test-project \
  GCP_REGION=us-central1 \
  API_IMAGE_URI="$api_image" \
  RUNTIME_SERVICE_ACCOUNT=api@test-project.iam.gserviceaccount.com \
  CLOUD_SQL_INSTANCE=test-project:us-central1:buglens \
  GCS_BUCKET=test-evidence \
  APP_BASE_URL=https://app.buglens.ai \
  GITHUB_APP_ID=123456 \
  GITHUB_APP_SLUG=buglens \
  DATABASE_URL_SECRET=buglens-database-url \
  DATABASE_URL_SECRET_VERSION=1 \
  SESSION_SECRET_SECRET=buglens-session-secret \
  SESSION_SECRET_SECRET_VERSION=1 \
  GITHUB_PRIVATE_KEY_SECRET=buglens-github-private-key \
  GITHUB_PRIVATE_KEY_SECRET_VERSION=1 \
  GITHUB_CLIENT_ID_SECRET=buglens-github-client-id \
  GITHUB_CLIENT_ID_SECRET_VERSION=1 \
  GITHUB_CLIENT_SECRET_SECRET=buglens-github-client-secret \
  GITHUB_CLIENT_SECRET_SECRET_VERSION=1 \
  GEMINI_API_KEY_SECRET=buglens-gemini-api-key \
  GEMINI_API_KEY_SECRET_VERSION=1 \
  "${script_directory}/deploy-api.sh"
assert_contains "$gcloud_log" "--image=${api_image}"
assert_contains "$gcloud_log" "--ingress=internal-and-cloud-load-balancing"
assert_contains "$gcloud_log" "--no-default-url"
assert_contains "$gcloud_log" "PLAYWRIGHT_ALLOW_PRIVATE_NETWORK=false"

: >"$gcloud_log"
env \
  PATH="$common_path" \
  GCLOUD_LOG="$gcloud_log" \
  GCP_PROJECT_ID=test-project \
  GCP_REGION=us-central1 \
  WEB_IMAGE_URI="$web_image" \
  WEB_RUNTIME_SERVICE_ACCOUNT=web@test-project.iam.gserviceaccount.com \
  "${script_directory}/deploy-web.sh"
assert_contains "$gcloud_log" "--image=${web_image}"
assert_contains "$gcloud_log" "--ingress=internal-and-cloud-load-balancing"
assert_contains "$gcloud_log" "--no-default-url"

: >"$gcloud_log"
env \
  PATH="$common_path" \
  GCLOUD_LOG="$gcloud_log" \
  GCP_PROJECT_ID=test-project \
  GCP_REGION=us-central1 \
  API_IMAGE_URI="$api_image" \
  MIGRATION_SERVICE_ACCOUNT=migrate@test-project.iam.gserviceaccount.com \
  CLOUD_SQL_INSTANCE=test-project:us-central1:buglens \
  DATABASE_URL_SECRET=buglens-database-url \
  DATABASE_URL_SECRET_VERSION=1 \
  "${script_directory}/deploy-migration-job.sh"
assert_contains "$gcloud_log" "--image=${api_image}"
assert_contains "$gcloud_log" "--tasks=1"
assert_contains "$gcloud_log" "--parallelism=1"
assert_contains "$gcloud_log" "--max-retries=0"

if env \
  PATH="$common_path" \
  GCLOUD_LOG="$gcloud_log" \
  GCP_PROJECT_ID=test-project \
  GCP_REGION=us-central1 \
  API_IMAGE_URI=us-central1-docker.pkg.dev/test-project/buglens/buglens-api:latest \
  MIGRATION_SERVICE_ACCOUNT=migrate@test-project.iam.gserviceaccount.com \
  CLOUD_SQL_INSTANCE=test-project:us-central1:buglens \
  DATABASE_URL_SECRET=buglens-database-url \
  DATABASE_URL_SECRET_VERSION=1 \
  "${script_directory}/deploy-migration-job.sh" >/dev/null 2>&1; then
  printf 'error: migration deployment accepted a mutable image tag.\n' >&2
  exit 1
fi

load_balancer_script="${script_directory}/configure-load-balancer.sh"
assert_contains "$load_balancer_script" "- /api"
assert_contains "$load_balancer_script" "- /api/*"
assert_contains "$load_balancer_script" "--load-balancing-scheme=EXTERNAL_MANAGED"
assert_contains "$load_balancer_script" "--network-tier=PREMIUM"
if grep -Fq -- '--url-rewrite' "$load_balancer_script"; then
  printf 'error: load balancer configuration must not rewrite URLs.\n' >&2
  exit 1
fi

if grep -Fq -- 'NEXT_PUBLIC_API_BASE_URL' "${script_directory}/deploy-web.sh"; then
  printf 'error: web deployment must keep the image default API base.\n' >&2
  exit 1
fi

printf 'GCP release script contracts passed.\n'
