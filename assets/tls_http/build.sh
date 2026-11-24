#!/usr/bin/env sh
# Build the Go transport that reproduces Bun's BoringSSL ClientHello.
# No Bun required at runtime. Output defaults to ./o2a-tls.
set -eu
cd "$(dirname "$0")"
OUT="${1:-./o2a-tls}"
GOOS="${GOOS:-$(go env GOOS)}"
GOARCH="${GOARCH:-$(go env GOARCH)}"
GOARM="${GOARM:-}"
echo "building $GOOS/$GOARCH$GOARM -> $OUT"
if [ -n "$GOARM" ]; then
  GOOS="$GOOS" GOARCH="$GOARCH" GOARM="$GOARM" CGO_ENABLED=0 \
    go build -trimpath -ldflags="-s -w" -o "$OUT" .
else
  GOOS="$GOOS" GOARCH="$GOARCH" CGO_ENABLED=0 \
    go build -trimpath -ldflags="-s -w" -o "$OUT" .
fi
