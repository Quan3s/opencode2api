#!/usr/bin/env sh
# Build the Go transport that reproduces Bun's BoringSSL ClientHello.
# Output defaults to ./o2a-tls, or pass a path as the first argument.
#
# Linux/macOS/Windows/FreeBSD use a static build (CGO_ENABLED=0).
# GOOS=android needs cgo + NDK; set ANDROID_NDK_HOME (auto-detected under
# /opt/android-sdk/ndk or $ANDROID_HOME/ndk). Without an NDK we fall back to
# a static linux binary, which still runs on Android/Termux.
set -eu
cd "$(dirname "$0")"

OUT="${1:-./o2a-tls}"
OS_IN="${GOOS:-$(go env GOOS)}"
ARCH_IN="${GOARCH:-$(go env GOARCH)}"
GOARM="${GOARM:-}"
CGO=0
CC_BIN=""

find_ndk() {
  if [ -n "${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}" ]; then
    printf '%s' "${ANDROID_NDK_HOME:-$ANDROID_NDK_ROOT}"
    return
  fi
  sdk="${ANDROID_HOME:-/opt/android-sdk}"
  if [ -d "$sdk/ndk" ]; then
    find "$sdk/ndk" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -1
  fi
}

if [ "$OS_IN" = "android" ]; then
  NDK="$(find_ndk)"
  if [ -n "$NDK" ] && [ -x "$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/clang" ]; then
    case "$ARCH_IN" in
      arm)   CC_NAME=armv7a-linux-androideabi24-clang ;;
      arm64) CC_NAME=aarch64-linux-android24-clang ;;
      386)   CC_NAME=i686-linux-android24-clang ;;
      amd64) CC_NAME=x86_64-linux-android24-clang ;;
      *) echo "unsupported android architecture: $ARCH_IN" >&2; exit 2 ;;
    esac
    CC_BIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/$CC_NAME"
    CGO=1
  else
    echo "note: no Android NDK found; using static linux target (works on Android)" >&2
    OS_IN=linux
  fi
fi

if [ "$ARCH_IN" = "arm" ] && [ -z "$GOARM" ]; then
  GOARM=7
fi

echo "building $OS_IN/$ARCH_IN${GOARM:+ GOARM=$GOARM} cgo=$CGO -> $OUT"
GOOS="$OS_IN" GOARCH="$ARCH_IN" GOARM="$GOARM" CGO_ENABLED="$CGO" CC="$CC_BIN" \
  go build -trimpath -ldflags="-s -w" -o "$OUT" .
