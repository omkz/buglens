#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'error: %s\n' "$1" >&2
  exit 1
}

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    fail "$name is required."
  fi
}

require_resource_name() {
  local name="$1"
  local value="${!name}"
  if [[ ! "$value" =~ ^[a-z]([-a-z0-9]*[a-z0-9])?$ ]]; then
    fail "$name must be a valid lowercase Google Cloud resource name."
  fi
}

assert_suffix() {
  local actual="$1"
  local expected_suffix="$2"
  local description="$3"
  if [[ "$actual" != *"$expected_suffix" ]]; then
    fail "$description conflicts with the expected Buglensa architecture."
  fi
}

normalize_list() {
  tr ';,' '\n' | sed '/^[[:space:]]*$/d; s/^[[:space:]]*//; s/[[:space:]]*$//' | sort | paste -sd, -
}

is_effectively_empty() {
  local value
  value="$(printf '%s' "$1" | tr -d '[:space:]')"
  case "${value,,}" in
    "" | "[]" | "{}" | "none" | "null") return 0 ;;
    *) return 1 ;;
  esac
}

command -v gcloud >/dev/null 2>&1 || fail "gcloud is required."

require_env GCP_PROJECT_ID
require_env GCP_REGION

CLOUD_RUN_SERVICE="${CLOUD_RUN_SERVICE:-buglens-api}"
WEB_CLOUD_RUN_SERVICE="${WEB_CLOUD_RUN_SERVICE:-buglens-web}"
WEB_NEG_NAME="${WEB_NEG_NAME:-buglens-web-neg}"
API_NEG_NAME="${API_NEG_NAME:-buglens-api-neg}"
WEB_BACKEND_SERVICE="${WEB_BACKEND_SERVICE:-buglens-web-backend}"
API_BACKEND_SERVICE="${API_BACKEND_SERVICE:-buglens-api-backend}"
URL_MAP_NAME="${URL_MAP_NAME:-buglens-url-map}"
HTTP_REDIRECT_URL_MAP_NAME="${HTTP_REDIRECT_URL_MAP_NAME:-buglens-http-redirect}"
PATH_MATCHER_NAME="${PATH_MATCHER_NAME:-buglens-paths}"
HTTPS_PROXY_NAME="${HTTPS_PROXY_NAME:-buglens-https-proxy}"
HTTP_PROXY_NAME="${HTTP_PROXY_NAME:-buglens-http-proxy}"
HTTPS_FORWARDING_RULE_NAME="${HTTPS_FORWARDING_RULE_NAME:-buglens-https}"
HTTP_FORWARDING_RULE_NAME="${HTTP_FORWARDING_RULE_NAME:-buglens-http}"
GLOBAL_IP_NAME="${GLOBAL_IP_NAME:-buglens-ip}"
SSL_CERTIFICATE_NAME="${SSL_CERTIFICATE_NAME:-buglens-app-cert}"
APP_DOMAIN="${APP_DOMAIN:-app.buglens.ai}"

resource_names=(
  CLOUD_RUN_SERVICE
  WEB_CLOUD_RUN_SERVICE
  WEB_NEG_NAME
  API_NEG_NAME
  WEB_BACKEND_SERVICE
  API_BACKEND_SERVICE
  URL_MAP_NAME
  HTTP_REDIRECT_URL_MAP_NAME
  PATH_MATCHER_NAME
  HTTPS_PROXY_NAME
  HTTP_PROXY_NAME
  HTTPS_FORWARDING_RULE_NAME
  HTTP_FORWARDING_RULE_NAME
  GLOBAL_IP_NAME
  SSL_CERTIFICATE_NAME
)

for name in "${resource_names[@]}"; do
  require_resource_name "$name"
done

if [[ ! "$GCP_PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  fail "GCP_PROJECT_ID is not a valid project ID."
fi

if [[ ! "$GCP_REGION" =~ ^[a-z]+-[a-z0-9]+[0-9]$ ]]; then
  fail "GCP_REGION is not a valid Google Cloud region."
fi

if [[ ! "$APP_DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ || "$APP_DOMAIN" != *.* ]]; then
  fail "APP_DOMAIN must be a valid lowercase DNS name."
fi

ensure_neg() {
  local neg_name="$1"
  local service_name="$2"

  if ! gcloud compute network-endpoint-groups describe "$neg_name" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" >/dev/null 2>&1; then
    gcloud compute network-endpoint-groups create "$neg_name" \
      --project="$GCP_PROJECT_ID" \
      --region="$GCP_REGION" \
      --network-endpoint-type=serverless \
      --cloud-run-service="$service_name" \
      --quiet
  fi

  local endpoint_type
  local actual_service
  local url_mask
  endpoint_type="$(gcloud compute network-endpoint-groups describe "$neg_name" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --format='value(networkEndpointType)')"
  actual_service="$(gcloud compute network-endpoint-groups describe "$neg_name" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --format='value(cloudRun.service)')"
  url_mask="$(gcloud compute network-endpoint-groups describe "$neg_name" \
    --project="$GCP_PROJECT_ID" \
    --region="$GCP_REGION" \
    --format='value(cloudRun.urlMask)')"

  [[ "${endpoint_type^^}" == "SERVERLESS" ]] || \
    fail "$neg_name exists but is not a serverless NEG."
  [[ "$actual_service" == "$service_name" ]] || \
    fail "$neg_name points to $actual_service instead of $service_name."
  [[ -z "$url_mask" ]] || fail "$neg_name unexpectedly uses a Cloud Run URL mask."
}

ensure_backend_service() {
  local backend_service="$1"
  local neg_name="$2"

  if ! gcloud compute backend-services describe "$backend_service" \
    --project="$GCP_PROJECT_ID" \
    --global >/dev/null 2>&1; then
    gcloud compute backend-services create "$backend_service" \
      --project="$GCP_PROJECT_ID" \
      --global \
      --load-balancing-scheme=EXTERNAL_MANAGED \
      --protocol=HTTP \
      --no-enable-cdn \
      --quiet
  fi

  local scheme
  local protocol
  local health_checks
  local cdn_enabled
  scheme="$(gcloud compute backend-services describe "$backend_service" \
    --project="$GCP_PROJECT_ID" --global --format='value(loadBalancingScheme)')"
  protocol="$(gcloud compute backend-services describe "$backend_service" \
    --project="$GCP_PROJECT_ID" --global --format='value(protocol)')"
  health_checks="$(gcloud compute backend-services describe "$backend_service" \
    --project="$GCP_PROJECT_ID" --global --format='value(healthChecks)')"
  cdn_enabled="$(gcloud compute backend-services describe "$backend_service" \
    --project="$GCP_PROJECT_ID" --global --format='value(enableCDN)')"

  [[ "$scheme" == "EXTERNAL_MANAGED" ]] || \
    fail "$backend_service has an unexpected load-balancing scheme."
  [[ "$protocol" == "HTTP" ]] || \
    fail "$backend_service has an unexpected protocol."
  [[ -z "$health_checks" ]] || \
    fail "$backend_service must not use health checks with a serverless NEG."
  [[ "${cdn_enabled,,}" != "true" ]] || \
    fail "$backend_service unexpectedly has Cloud CDN enabled."

  local backend_groups
  backend_groups="$(gcloud compute backend-services describe "$backend_service" \
    --project="$GCP_PROJECT_ID" \
    --global \
    --format='value(backends[].group)')"

  if [[ -z "$backend_groups" ]]; then
    gcloud compute backend-services add-backend "$backend_service" \
      --project="$GCP_PROJECT_ID" \
      --global \
      --network-endpoint-group="$neg_name" \
      --network-endpoint-group-region="$GCP_REGION" \
      --quiet
    backend_groups="$(gcloud compute backend-services describe "$backend_service" \
      --project="$GCP_PROJECT_ID" \
      --global \
      --format='value(backends[].group)')"
  fi

  if [[ "$backend_groups" == *';'* || "$backend_groups" == *','* || "$backend_groups" == *' '* ]]; then
    fail "$backend_service must contain exactly one backend."
  fi
  assert_suffix "$backend_groups" \
    "/regions/${GCP_REGION}/networkEndpointGroups/${neg_name}" \
    "$backend_service backend"
}

ensure_neg "$WEB_NEG_NAME" "$WEB_CLOUD_RUN_SERVICE"
ensure_neg "$API_NEG_NAME" "$CLOUD_RUN_SERVICE"
ensure_backend_service "$WEB_BACKEND_SERVICE" "$WEB_NEG_NAME"
ensure_backend_service "$API_BACKEND_SERVICE" "$API_NEG_NAME"

temporary_directory="$(mktemp -d)"
trap 'rm -rf "$temporary_directory"' EXIT
application_map_file="${temporary_directory}/application-url-map.yaml"
redirect_map_file="${temporary_directory}/redirect-url-map.yaml"
web_backend_uri="https://www.googleapis.com/compute/v1/projects/${GCP_PROJECT_ID}/global/backendServices/${WEB_BACKEND_SERVICE}"
api_backend_uri="https://www.googleapis.com/compute/v1/projects/${GCP_PROJECT_ID}/global/backendServices/${API_BACKEND_SERVICE}"

cat >"$application_map_file" <<EOF
name: ${URL_MAP_NAME}
defaultService: ${web_backend_uri}
hostRules:
- hosts:
  - ${APP_DOMAIN}
  pathMatcher: ${PATH_MATCHER_NAME}
pathMatchers:
- name: ${PATH_MATCHER_NAME}
  defaultService: ${web_backend_uri}
  pathRules:
  - paths:
    - /api
    - /api/*
    service: ${api_backend_uri}
tests:
- description: Web root routes to the web service
  host: ${APP_DOMAIN}
  path: /
  service: ${web_backend_uri}
- description: Web project pages route to the web service
  host: ${APP_DOMAIN}
  path: /projects
  service: ${web_backend_uri}
- description: API project routes reach FastAPI
  host: ${APP_DOMAIN}
  path: /api/projects
  service: ${api_backend_uri}
- description: API GitHub routes reach FastAPI
  host: ${APP_DOMAIN}
  path: /api/github/status
  service: ${api_backend_uri}
EOF

if ! gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global >/dev/null 2>&1; then
  gcloud compute url-maps import "$URL_MAP_NAME" \
    --project="$GCP_PROJECT_ID" \
    --global \
    --source="$application_map_file" \
    --quiet
fi

actual_default_service="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(defaultService)')"
actual_hosts="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(hostRules[0].hosts[0])')"
actual_host_matcher="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(hostRules[0].pathMatcher)')"
unexpected_host_rule="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(hostRules[1])')"
actual_matcher_name="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].name)')"
actual_matcher_default="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].defaultService)')"
unexpected_matcher="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[1])')"
actual_first_path="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].pathRules[0].paths[0])')"
actual_second_path="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].pathRules[0].paths[1])')"
unexpected_path="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].pathRules[0].paths[2])')"
actual_path_service="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].pathRules[0].service)')"
unexpected_path_rule="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].pathRules[1])')"
path_rule_host_rewrite="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].pathRules[0].routeAction.urlRewrite.hostRewrite)')"
path_rule_prefix_rewrite="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].pathRules[0].routeAction.urlRewrite.pathPrefixRewrite)')"
path_rule_template_rewrite="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].pathRules[0].routeAction.urlRewrite.pathTemplateRewrite)')"
route_rule_host_rewrite="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].routeRules[0].routeAction.urlRewrite.hostRewrite)')"
route_rule_prefix_rewrite="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].routeRules[0].routeAction.urlRewrite.pathPrefixRewrite)')"
route_rule_template_rewrite="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].routeRules[0].routeAction.urlRewrite.pathTemplateRewrite)')"
unexpected_route_rule="$(gcloud compute url-maps describe "$URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers[0].routeRules[0])')"

assert_suffix "$actual_default_service" "/global/backendServices/${WEB_BACKEND_SERVICE}" "$URL_MAP_NAME default service"
[[ "$actual_hosts" == "$APP_DOMAIN" ]] || \
  fail "$URL_MAP_NAME host rule conflicts with $APP_DOMAIN."
[[ "$actual_host_matcher" == "$PATH_MATCHER_NAME" ]] || \
  fail "$URL_MAP_NAME host rule points at an unexpected path matcher."
is_effectively_empty "$unexpected_host_rule" || fail "$URL_MAP_NAME contains an unexpected host rule."
[[ "$actual_matcher_name" == "$PATH_MATCHER_NAME" ]] || \
  fail "$URL_MAP_NAME contains an unexpected path matcher."
is_effectively_empty "$unexpected_matcher" || fail "$URL_MAP_NAME contains an unexpected path matcher."
assert_suffix "$actual_matcher_default" "/global/backendServices/${WEB_BACKEND_SERVICE}" "$URL_MAP_NAME matcher default"
[[ "$(printf '%s\n%s' "$actual_first_path" "$actual_second_path" | normalize_list)" == '/api,/api/*' ]] || \
  fail "$URL_MAP_NAME must route exactly /api and /api/*."
is_effectively_empty "$unexpected_path" || fail "$URL_MAP_NAME contains an unexpected API path."
assert_suffix "$actual_path_service" "/global/backendServices/${API_BACKEND_SERVICE}" "$URL_MAP_NAME API path service"
is_effectively_empty "$unexpected_path_rule" || fail "$URL_MAP_NAME must contain exactly one API path rule."
for rewrite in \
  "$path_rule_host_rewrite" \
  "$path_rule_prefix_rewrite" \
  "$path_rule_template_rewrite" \
  "$route_rule_host_rewrite" \
  "$route_rule_prefix_rewrite" \
  "$route_rule_template_rewrite"; do
  is_effectively_empty "$rewrite" || fail "$URL_MAP_NAME must not rewrite request URLs."
done
is_effectively_empty "$unexpected_route_rule" || fail "$URL_MAP_NAME contains unexpected route rules."

cat >"$redirect_map_file" <<EOF
name: ${HTTP_REDIRECT_URL_MAP_NAME}
defaultUrlRedirect:
  redirectResponseCode: MOVED_PERMANENTLY_DEFAULT
  httpsRedirect: true
tests:
- description: HTTP requests redirect to the same HTTPS URL
  host: ${APP_DOMAIN}
  path: /projects?from=http
  expectedOutputUrl: https://${APP_DOMAIN}/projects?from=http
  expectedRedirectResponseCode: 301
EOF

if ! gcloud compute url-maps describe "$HTTP_REDIRECT_URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global >/dev/null 2>&1; then
  gcloud compute url-maps import "$HTTP_REDIRECT_URL_MAP_NAME" \
    --project="$GCP_PROJECT_ID" \
    --global \
    --source="$redirect_map_file" \
    --quiet
fi

actual_https_redirect="$(gcloud compute url-maps describe "$HTTP_REDIRECT_URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(defaultUrlRedirect.httpsRedirect)')"
actual_redirect_code="$(gcloud compute url-maps describe "$HTTP_REDIRECT_URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(defaultUrlRedirect.redirectResponseCode)')"
redirect_host_rules="$(gcloud compute url-maps describe "$HTTP_REDIRECT_URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(hostRules)')"
redirect_path_matchers="$(gcloud compute url-maps describe "$HTTP_REDIRECT_URL_MAP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(pathMatchers)')"
[[ "${actual_https_redirect,,}" == "true" ]] || \
  fail "$HTTP_REDIRECT_URL_MAP_NAME does not redirect to HTTPS."
[[ "$actual_redirect_code" == "MOVED_PERMANENTLY_DEFAULT" ]] || \
  fail "$HTTP_REDIRECT_URL_MAP_NAME does not use a permanent redirect."
is_effectively_empty "$redirect_host_rules" || \
  fail "$HTTP_REDIRECT_URL_MAP_NAME must redirect every HTTP request."
is_effectively_empty "$redirect_path_matchers" || \
  fail "$HTTP_REDIRECT_URL_MAP_NAME must redirect every HTTP request."

if ! gcloud compute addresses describe "$GLOBAL_IP_NAME" \
  --project="$GCP_PROJECT_ID" --global >/dev/null 2>&1; then
  gcloud compute addresses create "$GLOBAL_IP_NAME" \
    --project="$GCP_PROJECT_ID" \
    --global \
    --ip-version=IPV4 \
    --network-tier=PREMIUM \
    --quiet
fi

ip_version="$(gcloud compute addresses describe "$GLOBAL_IP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(ipVersion)')"
ip_tier="$(gcloud compute addresses describe "$GLOBAL_IP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(networkTier)')"
load_balancer_ip="$(gcloud compute addresses describe "$GLOBAL_IP_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(address)')"
[[ "$ip_version" == "IPV4" ]] || fail "$GLOBAL_IP_NAME is not an IPv4 address."
[[ "$ip_tier" == "PREMIUM" ]] || fail "$GLOBAL_IP_NAME is not Premium tier."
[[ -n "$load_balancer_ip" ]] || fail "$GLOBAL_IP_NAME has no allocated address."

if ! gcloud compute ssl-certificates describe "$SSL_CERTIFICATE_NAME" \
  --project="$GCP_PROJECT_ID" --global >/dev/null 2>&1; then
  gcloud compute ssl-certificates create "$SSL_CERTIFICATE_NAME" \
    --project="$GCP_PROJECT_ID" \
    --global \
    --domains="$APP_DOMAIN" \
    --quiet
fi

certificate_type="$(gcloud compute ssl-certificates describe "$SSL_CERTIFICATE_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(type)')"
certificate_domains="$(gcloud compute ssl-certificates describe "$SSL_CERTIFICATE_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(managed.domains)')"
[[ "$certificate_type" == "MANAGED" ]] || \
  fail "$SSL_CERTIFICATE_NAME is not a Google-managed certificate."
[[ "$(printf '%s' "$certificate_domains" | normalize_list)" == "$APP_DOMAIN" ]] || \
  fail "$SSL_CERTIFICATE_NAME does not cover exactly $APP_DOMAIN."

if ! gcloud compute target-https-proxies describe "$HTTPS_PROXY_NAME" \
  --project="$GCP_PROJECT_ID" --global >/dev/null 2>&1; then
  gcloud compute target-https-proxies create "$HTTPS_PROXY_NAME" \
    --project="$GCP_PROJECT_ID" \
    --global \
    --url-map="$URL_MAP_NAME" \
    --ssl-certificates="$SSL_CERTIFICATE_NAME" \
    --quiet
fi

https_proxy_map="$(gcloud compute target-https-proxies describe "$HTTPS_PROXY_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(urlMap)')"
https_proxy_certificates="$(gcloud compute target-https-proxies describe "$HTTPS_PROXY_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(sslCertificates)')"
assert_suffix "$https_proxy_map" "/global/urlMaps/${URL_MAP_NAME}" "$HTTPS_PROXY_NAME URL map"
[[ "$https_proxy_certificates" != *';'* && "$https_proxy_certificates" != *','* && "$https_proxy_certificates" != *' '* ]] || \
  fail "$HTTPS_PROXY_NAME must use exactly one SSL certificate."
assert_suffix "$https_proxy_certificates" "/global/sslCertificates/${SSL_CERTIFICATE_NAME}" "$HTTPS_PROXY_NAME certificate"

if ! gcloud compute target-http-proxies describe "$HTTP_PROXY_NAME" \
  --project="$GCP_PROJECT_ID" --global >/dev/null 2>&1; then
  gcloud compute target-http-proxies create "$HTTP_PROXY_NAME" \
    --project="$GCP_PROJECT_ID" \
    --global \
    --url-map="$HTTP_REDIRECT_URL_MAP_NAME" \
    --quiet
fi

http_proxy_map="$(gcloud compute target-http-proxies describe "$HTTP_PROXY_NAME" \
  --project="$GCP_PROJECT_ID" --global --format='value(urlMap)')"
assert_suffix "$http_proxy_map" "/global/urlMaps/${HTTP_REDIRECT_URL_MAP_NAME}" "$HTTP_PROXY_NAME URL map"

ensure_forwarding_rule() {
  local rule_name="$1"
  local port="$2"
  local proxy_flag="$3"
  local proxy_name="$4"
  local target_resource="$5"

  if ! gcloud compute forwarding-rules describe "$rule_name" \
    --project="$GCP_PROJECT_ID" --global >/dev/null 2>&1; then
    gcloud compute forwarding-rules create "$rule_name" \
      --project="$GCP_PROJECT_ID" \
      --global \
      --load-balancing-scheme=EXTERNAL_MANAGED \
      --network-tier=PREMIUM \
      --address="$GLOBAL_IP_NAME" \
      "$proxy_flag=$proxy_name" \
      --ports="$port" \
      --quiet
  fi

  local scheme
  local tier
  local address
  local port_range
  local target
  local ip_protocol
  scheme="$(gcloud compute forwarding-rules describe "$rule_name" \
    --project="$GCP_PROJECT_ID" --global --format='value(loadBalancingScheme)')"
  tier="$(gcloud compute forwarding-rules describe "$rule_name" \
    --project="$GCP_PROJECT_ID" --global --format='value(networkTier)')"
  address="$(gcloud compute forwarding-rules describe "$rule_name" \
    --project="$GCP_PROJECT_ID" --global --format='value(IPAddress)')"
  port_range="$(gcloud compute forwarding-rules describe "$rule_name" \
    --project="$GCP_PROJECT_ID" --global --format='value(portRange)')"
  target="$(gcloud compute forwarding-rules describe "$rule_name" \
    --project="$GCP_PROJECT_ID" --global --format='value(target)')"
  ip_protocol="$(gcloud compute forwarding-rules describe "$rule_name" \
    --project="$GCP_PROJECT_ID" --global --format='value(IPProtocol)')"

  [[ "$scheme" == "EXTERNAL_MANAGED" ]] || fail "$rule_name has an unexpected load-balancing scheme."
  [[ "$tier" == "PREMIUM" ]] || fail "$rule_name is not Premium tier."
  [[ "$ip_protocol" == "TCP" ]] || fail "$rule_name does not use TCP."
  [[ "$address" == "$load_balancer_ip" ]] || fail "$rule_name does not use $GLOBAL_IP_NAME."
  [[ "$port_range" == "${port}-${port}" || "$port_range" == "$port" ]] || \
    fail "$rule_name does not listen only on port $port."
  assert_suffix "$target" "/global/${target_resource}/${proxy_name}" "$rule_name target proxy"
}

ensure_forwarding_rule "$HTTPS_FORWARDING_RULE_NAME" 443 \
  --target-https-proxy "$HTTPS_PROXY_NAME" targetHttpsProxies
ensure_forwarding_rule "$HTTP_FORWARDING_RULE_NAME" 80 \
  --target-http-proxy "$HTTP_PROXY_NAME" targetHttpProxies

printf 'Load balancer IPv4:\n%s\n\n' "$load_balancer_ip"
printf 'Required DNS record:\n%s  A  %s\n' "$APP_DOMAIN" "$load_balancer_ip"
