FROM node:24-bookworm-slim AS base

ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH

RUN corepack enable \
    && corepack prepare pnpm@11.5.3 --activate

FROM base AS deps

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

FROM base AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build

FROM node:24-bookworm-slim AS runner

ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=8080

RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid appgroup --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appgroup /app/.next/standalone ./
COPY --from=builder --chown=appuser:appgroup /app/.next/static ./.next/static
COPY --from=builder --chown=appuser:appgroup /app/public ./public

USER appuser

EXPOSE 8080

CMD ["node", "server.js"]
