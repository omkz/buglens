#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    printf 'error: %s is required.\n' "$name" >&2
    exit 1
  fi
}

command -v curl >/dev/null 2>&1 || {
  printf 'error: curl is required.\n' >&2
  exit 1
}

require_env APP_BASE_URL

if [[ "$APP_BASE_URL" != https://* ]]; then
  printf 'error: APP_BASE_URL must start with https://.\n' >&2
  exit 1
fi

if [[ "$APP_BASE_URL" == */ ]]; then
  printf 'error: APP_BASE_URL must not have a trailing slash.\n' >&2
  exit 1
fi

curl_options=(
  --connect-timeout 5
  --max-time 20
  --fail
  --silent
  --show-error
)

health_response="$(curl "${curl_options[@]}" "${APP_BASE_URL}/health")"
if [[ "$health_response" != '{"status":"ok"}' ]]; then
  printf 'error: web health response was unexpected.\n' >&2
  exit 1
fi

curl "${curl_options[@]}" --output /dev/null "${APP_BASE_URL}/"

github_status="$(curl "${curl_options[@]}" "${APP_BASE_URL}/api/github/status")"
if [[ "$github_status" != '{"connected":false,"installation_id":null,"account_login":null}' ]]; then
  printf 'error: API GitHub status did not report a disconnected JSON response.\n' >&2
  exit 1
fi

redirect_url="$(curl \
  --connect-timeout 5 \
  --max-time 20 \
  --silent \
  --show-error \
  --output /dev/null \
  --write-out '%{redirect_url}' \
  "http://${APP_BASE_URL#https://}/")"

if [[ "$redirect_url" != "${APP_BASE_URL}/" ]]; then
  printf 'error: HTTP did not redirect to the canonical HTTPS URL.\n' >&2
  exit 1
fi

printf 'Production routing verified for %s.\n' "$APP_BASE_URL"
