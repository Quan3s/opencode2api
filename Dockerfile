# syntax=docker/dockerfile:1

# Go TLS transport: reproduces Bun's BoringSSL ClientHello with utls.
# CGO_ENABLED=0 keeps the final image free of any runtime/toolchain dependency.
FROM golang:1.24-alpine AS tls
WORKDIR /src
COPY assets/tls_http/ ./
RUN ./build.sh /out/o2a-tls

FROM node:22-alpine AS dashboard
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    O2A_GO=/usr/local/bin/o2a-tls \
    UPSTREAM_MODE=opencode_free \
    OPENCODE_FREE_BASE_URL=https://opencode.ai/inference \
    CLIENT_API_KEY=changeme-local-key

COPY --from=tls /out/o2a-tls /usr/local/bin/o2a-tls
WORKDIR /app
COPY . .
COPY --from=dashboard /dashboard/dist /app/dashboard/dist

EXPOSE 8080
CMD ["python3", "app.py"]
