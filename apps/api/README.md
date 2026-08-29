# Buglensa API

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

Browser product routes are served from one public origin. FastAPI owns `/api/*`
while `/health`, `/ready`, `/docs`, and `/openapi.json` remain service-level
paths. Local Next.js development proxies `/api/*` to the API process on port
8000.

The GCP deployment script derives the three public URL settings from
`APP_BASE_URL=https://buglens.hakooi.com`. The resulting Cloud Run configuration
contains:

- `FRONTEND_BASE_URL=https://buglens.hakooi.com`
- `BACKEND_BASE_URL=https://buglens.hakooi.com/api`
- `GITHUB_CALLBACK_URL=https://buglens.hakooi.com/api/github/oauth/callback`
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

The GCP deployment scripts supply these values through Secret Manager:

- `DATABASE_URL`
- `SESSION_SECRET`
- the GitHub private key file referenced by `GITHUB_PRIVATE_KEY_PATH`
- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `GEMINI_API_KEY`

Never commit production secrets to `.env` files. Database migrations must run
as a separate deployment step or job; API instances do not run Alembic during
startup.
