# BugLens

**Show the bug. BugLens investigates, reproduces, and routes it.**

BugLens is an autonomous bug investigation agent. A user provides a screen
recording, optional voice context, and logs. BugLens analyzes the evidence,
inspects the connected GitHub repository, searches for duplicate issues,
creates a bounded reproduction plan, executes it with Playwright, and returns
the investigation result with evidence.

Built for the All Things Agentic Hackathon — Taskmaster track.

Production: <https://buglens.hakooi.com>

## What it does

1. Connects a repository through a GitHub App.
2. Accepts a screen recording with optional voice context, logs, or both.
3. Uses Gemini to extract bug details and reproduction context from the evidence.
4. Investigates relevant files in the connected repository.
5. Searches existing GitHub issues for possible duplicates.
6. Produces a bounded Playwright reproduction plan using a constrained action set.
7. Executes the plan and captures reproduction evidence.
8. Presents the analysis, repository findings, duplicate candidates, and reproduction result.
9. Creates a GitHub issue only after an explicit user action.

## Architecture

```text
Browser
  |
  v
Google Cloud Global Application Load Balancer
  |-- /*      -> Next.js / Cloud Run
  `-- /api/*  -> FastAPI / Cloud Run
                   |-- Google ADK + Gemini
                   |-- Playwright
                   |-- GitHub App / GitHub API
                   |-- Cloud SQL PostgreSQL
                   `-- Google Cloud Storage
```

Production is same-origin: the frontend and API are served from
`https://buglens.hakooi.com`, and browser requests use relative `/api/*` routes.

## Stack

**Web**

- Next.js 16
- React 19
- TypeScript
- Tailwind CSS

**API and agent**

- Python 3.14
- FastAPI
- Google ADK
- Gemini 3.6 Flash
- Playwright
- SQLAlchemy 2
- PostgreSQL
- Alembic
- structlog

**Infrastructure**

- Cloud Run
- Cloud SQL PostgreSQL
- Google Cloud Storage
- Secret Manager
- Artifact Registry
- Cloud Build
- Global External Application Load Balancer
- Google-managed TLS

## Repository structure

```text
.
├── apps/
│   ├── api/
│   └── web/
└── ops/
```

## Local development

The full flow requires local PostgreSQL and the required values from each
application's `.env.example`.

Start the API:

```sh
cd apps/api
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run playwright install chromium
uv run fastapi dev app/main.py
```

Start the web application in another terminal:

```sh
cd apps/web
pnpm install
pnpm dev
```

The web application runs at <http://localhost:3000> and FastAPI runs at
<http://localhost:8000>. During development, Next.js proxies `/api/*` requests
to port 8000.

## GitHub App permissions

- Metadata: read
- Contents: read
- Issues: read/write

## Safety and boundaries

- The model does not receive arbitrary shell execution.
- Browser reproduction uses a constrained action DSL.
- Production browser execution blocks private-network targets.
- GitHub issue creation requires explicit user action.
- Generate Fix and automatic pull requests are not part of the current core flow.
