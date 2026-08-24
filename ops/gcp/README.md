# BugLens Google Cloud deployment

These scripts update Cloud Run resources that already exist in a prepared
Google Cloud project. They do not build or push images, provision Cloud SQL or
Cloud Storage, create secrets, or grant prerequisite IAM roles.

Use an immutable image reference tagged with the source commit SHA, for example:

```text
REGION-docker.pkg.dev/PROJECT/buglens/buglens-api:GIT_SHA
```

Do not use `latest` for production releases.

## Release sequence

1. Build and push one immutable API image outside these scripts.
2. Set the variables below and run `./ops/gcp/deploy-migration-job.sh` with that image.
3. Run `./ops/gcp/run-migrations.sh` and wait for it to succeed.
4. Run `./ops/gcp/deploy-api.sh` with the same image.

Migration execution is deliberately separate from both job deployment and API
deployment. Schema changes must remain backward-compatible with the API revision
serving traffic during the rollout.

## Configuration

All scripts require `GCP_PROJECT_ID` and `GCP_REGION`. Deployment scripts also
require `IMAGE_URI`, `CLOUD_SQL_INSTANCE`, and their respective service account:

- `RUNTIME_SERVICE_ACCOUNT` for the API
- `MIGRATION_SERVICE_ACCOUNT` for migrations

`CLOUD_SQL_INSTANCE` uses the `PROJECT:REGION:INSTANCE` connection name. The API
deployment additionally requires:

- `GCS_BUCKET`
- `FRONTEND_BASE_URL`
- `BACKEND_BASE_URL`
- `GITHUB_CALLBACK_URL`
- `GITHUB_APP_ID`
- `GITHUB_APP_SLUG`

Optional resource settings and their defaults are:

- `CLOUD_RUN_SERVICE=buglens-api`
- `MIGRATION_JOB_NAME=buglens-migrate`
- `CLOUD_RUN_CPU=2`
- `CLOUD_RUN_MEMORY=2Gi`
- `CLOUD_RUN_CONCURRENCY=4`
- `CLOUD_RUN_MIN_INSTANCES=0`
- `CLOUD_RUN_MAX_INSTANCES=5`
- `CLOUD_RUN_TIMEOUT=900`

The scripts always pass the project and region explicitly and use non-interactive
`gcloud` commands. Cloud Run supplies `PORT`; do not configure it here.
The API deployment configures `/health` for startup and liveness checks and
`/ready` for database-backed readiness checks.

## Secret Manager

Create and manage the following secret IDs outside these scripts:

- `buglens-database-url`
- `buglens-session-secret`
- `buglens-github-private-key`
- `buglens-github-client-id`
- `buglens-github-client-secret`
- `buglens-gemini-api-key`

Supply each secret ID and a pinned numeric version separately:

- `DATABASE_URL_SECRET` and `DATABASE_URL_SECRET_VERSION`
- `SESSION_SECRET_SECRET` and `SESSION_SECRET_SECRET_VERSION`
- `GITHUB_PRIVATE_KEY_SECRET` and `GITHUB_PRIVATE_KEY_SECRET_VERSION`
- `GITHUB_CLIENT_ID_SECRET` and `GITHUB_CLIENT_ID_SECRET_VERSION`
- `GITHUB_CLIENT_SECRET_SECRET` and `GITHUB_CLIENT_SECRET_SECRET_VERSION`
- `GEMINI_API_KEY_SECRET` and `GEMINI_API_KEY_SECRET_VERSION`

Versions such as `1`, `2`, or `3` are required; the scripts reject `latest`.
Secret contents are never read or expanded by the deployment scripts. The
GitHub App private key is mounted at
`/var/secrets/buglens/github-private-key.pem` and is not exposed as an
environment variable.

Add secret versions from standard input so values do not appear in command-line
arguments:

```sh
printf '%s' "$VALUE" | gcloud secrets versions add SECRET --data-file=-
```

Never add a version with
`gcloud secrets versions add ... --data-file=<committed-secret-file>` when the
file could enter Git history. Do not store production values in committed `.env`
files.

The production database URL exists only in Secret Manager and has this shape:

```text
postgresql+psycopg://USER:PASSWORD@/buglens?host=/cloudsql/PROJECT:REGION:INSTANCE
```

## IAM prerequisites

Grant permissions before using the scripts; the scripts never grant these roles.

The runtime service account needs:

- Cloud SQL Client (`roles/cloudsql.client`)
- Secret Manager Secret Accessor (`roles/secretmanager.secretAccessor`) on only
  the six BugLens secrets
- `roles/storage.objectUser` on only the configured evidence bucket

The migration service account needs only:

- Cloud SQL Client (`roles/cloudsql.client`)
- Secret Manager Secret Accessor (`roles/secretmanager.secretAccessor`) on only
  the database URL secret

It does not need Cloud Storage, GitHub, or Gemini access. The deployment identity
also needs permission to deploy Cloud Run resources and act as the selected
runtime and migration service accounts.

Browser users call the API directly, so the Cloud Run service remains publicly
reachable. BugLens application and session authorization continues to protect
user data.
