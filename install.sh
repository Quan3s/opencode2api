#!/usr/bin/env sh
# Install the matching Go TLS helper from the latest GitHub Release.
# Usage: ./install.sh [destination]   (or O2A_INSTALL_PATH=/path/to/o2a-tls ./install.sh)
set -eu

VERSION="${O2A_VERSION:-v1.1.0}"
BASE="https://github.com/Quan3s/opencode2api/releases/download/$VERSION"

usage() {
  cat <<'EOF'
Usage: ./install.sh [destination]

Downloads the Go TLS helper binary matching this machine from the GitHub
Release and verifies it against SHA256SUMS.txt.

Environment:
  O2A_VERSION        release tag (default: v1.1.0)
  O2A_INSTALL_PATH   destination path (default: ./o2a-tls, or
                     assets/tls_http/o2a-tls when run inside the repo)
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$SCRIPT_DIR/assets/tls_http/main.go" ] && [ -z "${1:-}" ] && [ -z "${O2A_INSTALL_PATH:-}" ]; then
  DEST="$SCRIPT_DIR/assets/tls_http/o2a-tls"
else
  DEST="${1:-${O2A_INSTALL_PATH:-./o2a-tls}}"
fi

detect_platform() {
  case "$(uname -s)" in
    Linux)
      if [ -d /data/data/com.termux ] || [ -n "${TERMUX_VERSION:-}" ] || \
         [ "$(uname -o 2>/dev/null || true)" = "Android" ]; then
        PLATFORM=android
      else
        PLATFORM=linux
      fi
      ;;
    Darwin) PLATFORM=darwin ;;
    FreeBSD) PLATFORM=freebsd ;;
    MINGW*|MSYS*|CYGWIN*|Windows_NT) PLATFORM=windows ;;
    *) echo "error: unsupported OS: $(uname -s)" >&2; exit 1 ;;
  esac

  case "$(uname -m)" in
    x86_64|amd64) ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    armv7l|armv7|armhf) ARCH=armv7 ;;
    armv6l|armv6) ARCH=armv6 ;;
    i386|i486|i586|i686) ARCH=386 ;;
    *) echo "error: unsupported architecture: $(uname -m)" >&2; exit 1 ;;
  esac

  case "$PLATFORM/$ARCH" in
    android/armv6|darwin/386|darwin/armv6|darwin/armv7|freebsd/armv6|freebsd/armv7|windows/armv6|windows/armv7)
      echo "error: no release binary for $PLATFORM/$ARCH" >&2
      exit 1
      ;;
  esac

  ASSET="o2a-tls-$PLATFORM-$ARCH"
  if [ "$PLATFORM" = windows ]; then
    ASSET="$ASSET.exe"
  fi
}

download() {
  if command -v curl >/dev/null 2>&1 && curl -fsSL "$1" -o "$2"; then
    return 0
  fi
  if command -v wget >/dev/null 2>&1; then
    wget -qO "$2" "$1"
    return 0
  fi
  echo "error: curl or wget is required" >&2
  exit 1
}

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

detect_platform
TMP="${TMPDIR:-/tmp}/o2a-install.$$"
trap 'rm -f "$TMP.bin" "$TMP.sums"' EXIT HUP INT TERM

echo "downloading $ASSET from $VERSION"
download "$BASE/$ASSET" "$TMP.bin"
download "$BASE/SHA256SUMS.txt" "$TMP.sums"

EXPECTED=$(grep "  $ASSET\$" "$TMP.sums" | awk '{print $1}' || true)
ACTUAL=$(hash_file "$TMP.bin" || true)
if [ -n "$EXPECTED" ] && [ -n "$ACTUAL" ]; then
  if [ "$EXPECTED" != "$ACTUAL" ]; then
    echo "error: SHA256 mismatch (expected $EXPECTED, got $ACTUAL)" >&2
    exit 1
  fi
  echo "sha256 verified: $ACTUAL"
else
  echo "warning: no checksum tool available; skipped SHA256 verification" >&2
fi

mkdir -p "$(dirname "$DEST")"
cp "$TMP.bin" "$DEST"
chmod +x "$DEST"
echo "installed: $DEST"

if [ "$DEST" = "$SCRIPT_DIR/assets/tls_http/o2a-tls" ]; then
  echo "next: ./bin/o2a serve --port 8080"
else
  echo "next: O2A_GO=$DEST ./bin/o2a serve --port 8080"
fi
