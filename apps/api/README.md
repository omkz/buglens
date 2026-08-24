# BugLens API

Local development remains uv-based and does not require Docker:

```sh
uv run fastapi dev app/main.py
```

## Cloud Run production configuration

The container listens on Cloud Run's `PORT` value and defaults to port 8080
for local runs. Attach Cloud SQL to the Cloud Run service and keep
`DATABASE_URL` as the only database connection setting. A Unix-socket URL has
this placeholder-only shape:

```text
postgresql+psycopg://USER:PASSWORD@/buglens?host=/cloudsql/PROJECT:REGION:INSTANCE
```

Set these non-secret values in the Cloud Run service configuration:

- `FRONTEND_BASE_URL`
- `BACKEND_BASE_URL`
- `GITHUB_CALLBACK_URL`
- `GITHUB_APP_ID`
- `GITHUB_APP_SLUG`
- `LOG_LEVEL`
- `LOG_FORMAT=json`
- `EVIDENCE_STORAGE_BACKEND=gcs`
- `GCS_BUCKET`
- `DATABASE_POOL_SIZE=5`
- `DATABASE_MAX_OVERFLOW=2`
- `DATABASE_POOL_TIMEOUT_SECONDS=30`
- `DATABASE_POOL_RECYCLE_SECONDS=1800`
- `SESSION_COOKIE_SECURE=true`
- `PLAYWRIGHT_ALLOW_PRIVATE_NETWORK=false`

The following secrets will be supplied through Secret Manager in a later
deployment change:

- `DATABASE_URL`
- `SESSION_SECRET`
- `GITHUB_PRIVATE_KEY`
- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `GEMINI_API_KEY`

Never commit production secrets to `.env` files. Database migrations must run
as a separate deployment step or job; API instances do not run Alembic during
startup.
