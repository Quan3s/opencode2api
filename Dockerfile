# syntax=docker/dockerfile:1

FROM oven/bun:1.4 AS bun

FROM node:22-alpine AS dashboard
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    O2A_BUN=/usr/local/bin/bun \
    UPSTREAM_MODE=opencode_free \
    OPENCODE_FREE_BASE_URL=https://opencode.ai/inference \
    CLIENT_API_KEY=changeme-local-key

COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
WORKDIR /app
COPY . .
COPY --from=dashboard /dashboard/dist /app/dashboard/dist

EXPOSE 8080
CMD ["python3", "app.py"]
