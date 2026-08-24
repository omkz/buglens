# BugLens web

## Local development

Start the frontend with:

```bash
pnpm dev
```

Open <http://localhost:3000>. Browser requests to `/api/*` are rewritten by
Next.js in development to `http://localhost:8000/api/*`.

## Production routing

The production load balancer routes `https://app.buglens.ai/*` to the web Cloud
Run service and `https://app.buglens.ai/api/*` to the API Cloud Run service. The
frontend uses the relative `/api` base and does not require a backend hostname.

## Container

Build and run the standalone production image from this directory:

```bash
docker build -t buglens-web .
docker run --rm \
  -e PORT=8080 \
  -p 8080:8080 \
  buglens-web
```
