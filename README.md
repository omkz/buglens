# Buglensa

**Show the bug. Buglensa turns the evidence into a reviewable pull request.**

Buglensa is an autonomous bug investigation agent that connects bug evidence to
the repository where the problem lives. Its complete workflow is:

`Report Bug → Analyze → Investigate → Reproduce → Proposed Fix → optional Validate Fix → Create PR`

Fix Validation is informational and optional. A user can explicitly create a
pull request from a persisted Proposed Fix whether validation passed, failed,
was blocked, or was not run.

## Why Buglensa

Developers often spend significant time converting vague reports, recordings,
logs, and screenshots into actionable repository context. Buglensa turns that
translation work into one autonomous workflow: it understands the report,
investigates the code, reproduces the failure, proposes a concrete fix, and—only
after explicit user approval—creates a reviewable pull request.

## Hackathon track

- **Event:** All Things Agentic Hackathon
- **Category:** Taskmaster

Buglensa fits Taskmaster because it autonomously performs a multi-step
engineering workflow and takes real, bounded actions across repository
inspection, browser execution, and GitHub.

## What it does

1. Collects multimodal bug evidence: screen recordings, optional voice context,
   and logs.
2. Uses Gemini through Vertex AI to turn that evidence into a structured bug
   analysis.
3. Autonomously investigates relevant files in the connected GitHub repository.
4. Searches existing GitHub issues for likely duplicates.
5. Builds and runs a constrained Playwright reproduction plan.
6. Produces a structured Proposed Fix and renders its diff deterministically
   from trusted repository content and proposed replacement content.
7. Optionally applies the proposal in an isolated workspace and runs bounded Fix
   Validation checks.
8. Creates a pull request only when the user explicitly selects **Create PR**.
9. Writes only to an investigation-scoped branch; it never writes directly to
   the repository default branch.

## Architecture

```text
Browser
  |
  v
Google Cloud Global Application Load Balancer
  |-- /*      -> Next.js / Cloud Run
  `-- /api/*  -> FastAPI / Cloud Run
                   |-- Google ADK -> Gemini through Vertex AI
                   |-- GitHub App / GitHub API
                   |-- Playwright
                   |-- Cloud SQL PostgreSQL
                   `-- Google Cloud Storage
```

- **Next.js** captures reports and evidence, displays live progress and results,
  and exposes explicit publication actions.
- **FastAPI** authenticates GitHub installations, orchestrates analysis and
  investigation, enforces safety checks, and persists operation state.
- **Google ADK and Gemini through Vertex AI** perform structured evidence
  analysis and repository investigation.
- **GitHub App / GitHub API** provide installation-scoped repository access,
  duplicate search, and explicit issue or pull-request publication.
- **Playwright** executes only the constrained browser action plan.
- **Cloud SQL PostgreSQL** stores projects, investigations, results, proposals,
  validation state, and publication state.
- **Google Cloud Storage** stores production evidence without loading complete
  recordings into API memory.

Production is same-origin: the frontend and API are served from
<https://buglens.hakooi.com>, with `/api/*` routed to FastAPI.

## Google Cloud

Buglensa uses:

- **Cloud Run** for the Next.js web application and FastAPI API.
- **Cloud SQL PostgreSQL** for durable application and workflow state.
- **Google Cloud Storage** for production evidence.
- **Vertex AI / Gemini** for multimodal analysis and autonomous investigation.

## Stack

- **Web:** Next.js 16, React 19, TypeScript, Tailwind CSS
- **API and agent:** Python 3.14, FastAPI, Google ADK, Gemini, Playwright
- **Data:** SQLAlchemy 2, PostgreSQL, Alembic, Google Cloud Storage
- **Operations:** Cloud Run, Cloud SQL, Vertex AI, structured logging

## Repository structure

```text
.
├── apps/
│   ├── api/
│   └── web/
└── ops/
```

## Safety and execution boundaries

- Repository access uses GitHub App installation-scoped credentials and
  short-lived installation tokens.
- The model does not receive arbitrary shell execution.
- Browser reproduction uses a constrained action DSL, and production browser
  execution blocks private-network targets.
- A Proposed Fix is validated and persisted before it can be published.
- **Create PR** always requires explicit user action; Fix Validation does not
  trigger publication automatically.
- Before creating Git objects, a branch, or a PR, Buglensa resolves the exact
  current default-branch SHA and compares every proposed file with its persisted
  original content.
- Stale proposals fail safely without creating a branch or pull request.
- Buglensa creates a deterministic investigation-scoped `buglensa/fix-*` branch
  and never updates the repository default branch directly.
- Existing deterministic branches are never force-updated. They are used only
  when Buglensa can safely reconcile them to the same persisted investigation.

## GitHub App permissions

Required repository permissions:

- Metadata: Read-only
- Contents: Read and write
- Issues: Read and write
- Pull requests: Read and write

## Reproducible testing

Use [omkz/buglens-demo-target](https://github.com/omkz/buglens-demo-target) as
the connected demo repository. Keep real credentials out of the repository and
configure only local/development values from the supplied `.env.example` files.

1. Start PostgreSQL. With a local PostgreSQL installation, create a `buglens`
   database and match `DATABASE_URL` in `apps/api/.env`. One disposable Docker
   option is:

   ```sh
   docker run --name buglensa-postgres \
     -e POSTGRES_PASSWORD=postgres \
     -e POSTGRES_DB=buglens \
     -p 5432:5432 -d postgres:17
   ```

2. Configure and start the API:

   ```sh
   cd apps/api
   cp .env.example .env
   # Fill in local GitHub App settings, a random SESSION_SECRET, and Google ADC.
   # For localhost browser reproduction only:
   # PLAYWRIGHT_ALLOW_PRIVATE_NETWORK=true

   uv sync
   uv run alembic upgrade head
   uv run playwright install chromium
   uv run fastapi dev app/main.py
   ```

3. In another terminal, configure and start the web application:

   ```sh
   cd apps/web
   cp .env.example .env
   pnpm install
   pnpm dev
   ```

   Next.js runs at <http://localhost:3000>, proxies `/api/*` to FastAPI at
   <http://localhost:8000>, and should use `NEXT_PUBLIC_API_BASE_URL=/api`.

4. Clone and start the demo target in a separate terminal using its repository
   instructions:

   ```sh
   git clone https://github.com/omkz/buglens-demo-target.git
   cd buglens-demo-target
   ```

   Keep it on a different local port and note its application URL (for example,
   `http://localhost:3001`).

5. Install/connect the Buglensa GitHub App to `omkz/buglens-demo-target`, then
   create a Buglensa project for that repository and set its App URL to the local
   demo-target URL.

6. Create an investigation describing the cart **Checkout** navigation bug and
   attach any desired recording or logs. Select **Analyze**, then run the
   autonomous investigation.

7. Confirm that the result reproduces the bug and that **Proposed Fix** changes:

   ```diff
   - router.prefetch("/checkout")
   + router.push("/checkout")
   ```

8. Select **Create PR**. Fix Validation may be run first, but it is not required
   for this reproducibility flow.

9. Verify that Buglensa creates a `buglensa/fix-*` branch and opens a pull
   request into `main`, without modifying `main` directly.

Local reproduction against `localhost` requires
`PLAYWRIGHT_ALLOW_PRIVATE_NETWORK=true`. This is a development-only setting;
do not copy production credentials or enable private-network browser access in
production.

## Demo proof

- **Production application:** <https://buglens.hakooi.com>
- **Demo target repository:** <https://github.com/omkz/buglens-demo-target>
- **Buglensa-generated PR:** <https://github.com/omkz/buglens-demo-target/pull/4>

PR #4 demonstrates Buglensa applying the persisted fix on an isolated
`buglensa/fix-*` branch and opening a reviewable PR without modifying `main`
directly.
