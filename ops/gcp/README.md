# Buglensa Google Cloud production release

These scripts build immutable API and web images, deploy existing Buglensa
resources, and reconcile the first-region production load balancer. They do not
create Cloud SQL, Cloud Storage buckets, service accounts, IAM grants, secret
contents, or DNS records. They never run automatically from CI.

Production has one public origin:

```text
https://app.buglens.ai/*       -> buglens-web
https://app.buglens.ai/api     -> buglens-api
https://app.buglens.ai/api/*   -> buglens-api
```

The global external Application Load Balancer preserves every request path.
Cloud Run default URLs are disabled, and both services accept ingress only from
internal sources and Cloud Load Balancing.

## Prerequisites

Authenticate `gcloud` as a release identity and select a project whose required
resources already exist. Enable these APIs separately from the release scripts:

- `run.googleapis.com`
- `aiplatform.googleapis.com`
- `sqladmin.googleapis.com`
- `secretmanager.googleapis.com`
- `storage.googleapis.com`
- `artifactregistry.googleapis.com`
- `cloudbuild.googleapis.com`
- `compute.googleapis.com`

Create the Artifact Registry repository once:

```bash
gcloud artifacts repositories create buglens \
  --repository-format=docker \
  --location="$GCP_REGION" \
  --project="$GCP_PROJECT_ID"
```

Cloud SQL, the evidence bucket, service accounts, Secret Manager secrets, and
their IAM bindings must also exist before the first release.

## Release workflow

Use this order for every production release:

1. Confirm Artifact Registry, Cloud SQL, GCS, service accounts, and secrets exist.
2. Build and push both immutable images with `./ops/gcp/build-images.sh`.
3. Export the returned `API_IMAGE_URI` and `WEB_IMAGE_URI` values.
4. Deploy the migration job with `./ops/gcp/deploy-migration-job.sh`.
5. Run migrations explicitly with `./ops/gcp/run-migrations.sh`.
6. Deploy the API with `./ops/gcp/deploy-api.sh`.
7. Deploy the web service with `./ops/gcp/deploy-web.sh`.
8. Reconcile the load balancer with `./ops/gcp/configure-load-balancer.sh`.
9. Add or update the DNS A record printed by the load-balancer script.
10. Wait for the Google-managed certificate to become `ACTIVE`.
11. Run `./ops/gcp/verify-production.sh`.

Migrations never run during API startup. Schema changes must remain
backward-compatible with the API revision serving traffic during rollout.

## Build configuration

`build-images.sh` requires `GCP_PROJECT_ID` and `GCP_REGION`. It derives a full
40-character `GIT_SHA` from `git rev-parse HEAD`, unless an explicit full SHA is
provided, and submits both Docker contexts to Cloud Build. The default Artifact
Registry repository is `ARTIFACT_REPOSITORY=buglens`.

The resulting immutable references have these shapes:

```text
REGION-docker.pkg.dev/PROJECT/buglens/buglens-api:GIT_SHA
REGION-docker.pkg.dev/PROJECT/buglens/buglens-web:GIT_SHA
```

Do not use mutable `latest` tags for production releases.

## Cloud Run deployment configuration

All deployment scripts require `GCP_PROJECT_ID` and `GCP_REGION`.

The API and migration job both require the exact same `API_IMAGE_URI`. The API
also requires:

- `RUNTIME_SERVICE_ACCOUNT`
- `CLOUD_SQL_INSTANCE` in `PROJECT:REGION:INSTANCE` form
- `GCS_BUCKET`
- `APP_BASE_URL=https://app.buglens.ai` without a trailing slash
- `GITHUB_APP_ID`
- `GITHUB_APP_SLUG`
- `GOOGLE_CLOUD_PROJECT` (defaults to `GCP_PROJECT_ID`)
- `GOOGLE_CLOUD_LOCATION` (defaults to `global`)

The API derives its frontend, backend, and GitHub OAuth callback URLs from
`APP_BASE_URL`. It keeps the existing Cloud SQL attachment, GCS backend,
database pool settings, secure session cookie, GitHub PEM file mount, and
`PLAYWRIGHT_ALLOW_PRIVATE_NETWORK=false`. Its startup and liveness probes call
`/health`; readiness calls `/ready`.

The web deployment requires `WEB_IMAGE_URI` and
`WEB_RUNTIME_SERVICE_ACCOUNT`. It has no Cloud SQL attachment, storage access,
application secrets, or API-host environment variable. Its standalone image
uses the relative `/api` default. Startup and liveness probes call `/health`.

Optional Cloud Run names and sizing defaults are:

- `CLOUD_RUN_SERVICE=buglens-api`
- `WEB_CLOUD_RUN_SERVICE=buglens-web`
- `MIGRATION_JOB_NAME=buglens-migrate`
- API: `CLOUD_RUN_CPU=2`, `CLOUD_RUN_MEMORY=2Gi`,
  `CLOUD_RUN_CONCURRENCY=4`, `CLOUD_RUN_MIN_INSTANCES=0`,
  `CLOUD_RUN_MAX_INSTANCES=5`, `CLOUD_RUN_TIMEOUT=900`
- Web: `WEB_CLOUD_RUN_CPU=1`, `WEB_CLOUD_RUN_MEMORY=512Mi`,
  `WEB_CLOUD_RUN_CONCURRENCY=80`, `WEB_CLOUD_RUN_MIN_INSTANCES=0`,
  `WEB_CLOUD_RUN_MAX_INSTANCES=5`, `WEB_CLOUD_RUN_TIMEOUT=300`

Cloud Run supplies `PORT`; do not configure it in deployment scripts.

## Secret Manager

Create and manage these secret IDs outside the scripts:

- `buglens-database-url`
- `buglens-session-secret`
- `buglens-github-private-key`
- `buglens-github-client-id`
- `buglens-github-client-secret`

Supply every secret ID and a pinned numeric version separately:

- `DATABASE_URL_SECRET` and `DATABASE_URL_SECRET_VERSION`
- `SESSION_SECRET_SECRET` and `SESSION_SECRET_SECRET_VERSION`
- `GITHUB_PRIVATE_KEY_SECRET` and `GITHUB_PRIVATE_KEY_SECRET_VERSION`
- `GITHUB_CLIENT_ID_SECRET` and `GITHUB_CLIENT_ID_SECRET_VERSION`
- `GITHUB_CLIENT_SECRET_SECRET` and `GITHUB_CLIENT_SECRET_SECRET_VERSION`

Versions such as `1`, `2`, or `3` are required; `latest` is rejected. The GitHub
private key is mounted at `/var/secrets/buglens/github-private-key.pem`, never
placed in an environment variable.

Add secret versions through standard input so values do not appear in command
arguments:

```bash
printf '%s' "$VALUE" | gcloud secrets versions add SECRET --data-file=-
```

Never use a potentially committed secret file as `--data-file`, and never put
production secrets in committed `.env` files. The database URL lives only in
Secret Manager and has this shape:

```text
postgresql+psycopg://USER:PASSWORD@/buglens?host=/cloudsql/PROJECT:REGION:INSTANCE
```

## IAM

The scripts do not grant IAM roles.

The API runtime service account needs Cloud SQL Client, Secret Manager Secret
Accessor on only the five Buglensa secrets, bucket-level
`roles/storage.objectUser` on only the evidence bucket, and
`roles/aiplatform.user` for Vertex AI Gemini access.

The migration service account needs Cloud SQL Client and Secret Manager Secret
Accessor only for the database URL. It does not need GCS, GitHub, or Gemini
access.

The separate web runtime service account needs no application resource roles.
It does not need Cloud SQL Client, Secret Manager access, Storage Object User,
Vertex AI access, or GitHub access.

The release identity needs permission to submit Cloud Builds, deploy Cloud Run
services and jobs, reconcile the Compute load-balancer resources, and act as the
selected runtime and migration service accounts. Use user credentials or
service-account impersonation through `gcloud`; do not create service-account
keys.

Both browser-facing services deliberately allow unauthenticated invocation.
Restricted ingress prevents direct internet bypass while the load balancer can
invoke them. Buglensa session authorization continues to protect user data.

## Load balancer and DNS

`configure-load-balancer.sh` requires `GCP_PROJECT_ID` and `GCP_REGION`. Both
Cloud Run services and their serverless NEGs must use that same region. Defaults
are:

- NEGs: `buglens-web-neg`, `buglens-api-neg`
- backend services: `buglens-web-backend`, `buglens-api-backend`
- URL maps: `buglens-url-map`, `buglens-http-redirect`
- proxies: `buglens-https-proxy`, `buglens-http-proxy`
- forwarding rules: `buglens-https`, `buglens-http`
- global IPv4 address: `buglens-ip`
- managed certificate: `buglens-app-cert`
- domain: `APP_DOMAIN=app.buglens.ai`

The script is rerunnable: it creates missing named resources and validates
existing ones before reuse. It fails on conflicting service, backend, routing,
proxy, address, certificate, or forwarding-rule configuration. It does not
rewrite URLs, enable Cloud CDN, or mutate DNS.

External Application Load Balancer backend services using serverless NEGs do
not use Compute Engine health checks. Cloud Run startup, liveness, and readiness
probes remain responsible for revision health.

After the script prints the reserved IPv4 address, add this record with the DNS
provider:

```text
app.buglens.ai  A  LOAD_BALANCER_IPV4
```

Google-managed certificate provisioning can remain `PROVISIONING` until DNS
points to the load balancer, and can take time to become `ACTIVE`. Inspect it
with:

```bash
gcloud compute ssl-certificates describe "$SSL_CERTIFICATE_NAME" \
  --project="$GCP_PROJECT_ID" \
  --global \
  --format='yaml(managed.status,managed.domainStatus)'
```

The load balancer uses one Premium-tier global IPv4 address. Port 443 terminates
TLS; port 80 only performs a permanent HTTP-to-HTTPS redirect while preserving
the host, path, and query string.

## Production verification

Set `APP_BASE_URL=https://app.buglens.ai` and run
`./ops/gcp/verify-production.sh`. It uses bounded requests to verify the web
health endpoint, web root, unauthenticated GitHub connection status through the
`/api` route, and the HTTP-to-HTTPS redirect. API `/ready` remains an internal
Cloud Run probe and is intentionally not exposed as `/api/ready`.
