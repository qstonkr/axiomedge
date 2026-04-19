# syntax=docker/dockerfile:1.7
#
# axiomedge web — Next.js 16 standalone runtime.
#
# Build (from repo root):
#     make web-docker-build              # tag: axiomedge-web:latest
#     docker run --rm -p 3000:3000 \
#       -e API_URL=http://host.docker.internal:8000 \
#       axiomedge-web:latest
#
# Three stages:
#   1. deps  — install pnpm + pull lockfile-pinned modules
#   2. build — `next build` → .next/standalone (self-contained server)
#   3. run   — minimal alpine-node carrying just standalone + static + public
# ──────────────────────────────────────────────────────────────────────────

ARG NODE_VERSION=22-alpine

# ── 1. deps ──────────────────────────────────────────────────────────────
FROM node:${NODE_VERSION} AS deps
WORKDIR /app
RUN apk add --no-cache libc6-compat
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN corepack enable pnpm \
 && pnpm install --frozen-lockfile

# ── 2. build ─────────────────────────────────────────────────────────────
FROM node:${NODE_VERSION} AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN corepack enable pnpm \
 && pnpm build

# ── 3. run ───────────────────────────────────────────────────────────────
FROM node:${NODE_VERSION} AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -g 1001 nextjs \
 && adduser -S -u 1001 -G nextjs nextjs

# Standalone bundle ships server.js + a slim node_modules. We add /public
# and /.next/static manually because the trace doesn't include them.
COPY --from=build --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nextjs /app/.next/static ./.next/static
COPY --from=build --chown=nextjs:nextjs /app/public ./public

USER nextjs
EXPOSE 3000

# server.js is the standalone entry point Next.js generates.
CMD ["node", "server.js"]
