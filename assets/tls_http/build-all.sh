#!/usr/bin/env sh
# Build the Go TLS transport for every release target into ./dist.
# Desktop/server targets are static (CGO_ENABLED=0). Android targets are real
# GOOS=android builds and need an NDK (ANDROID_NDK_HOME, or auto-detected).
set -eu
cd "$(dirname "$0")"

OUT="${1:-./dist}"
mkdir -p "$OUT"

build() {
  os="$1"; arch="$2"; arm="$3"; name="$4"
  echo "building $name ($os/$arch${arm:+ GOARM=$arm})"
  if [ -n "$arm" ]; then
    GOOS="$os" GOARCH="$arch" GOARM="$arm" CGO_ENABLED=0 \
      go build -trimpath -ldflags="-s -w" -o "$OUT/$name" .
  else
    GOOS="$os" GOARCH="$arch" CGO_ENABLED=0 \
      go build -trimpath -ldflags="-s -w" -o "$OUT/$name" .
  fi
}

build_android() {
  arch="$1"; cc="$2"; name="$3"
  echo "building $name (android/$arch, cgo via NDK)"
  CC="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/$cc" \
    GOOS=android GOARCH="$arch" GOARM=7 CGO_ENABLED=1 \
    go build -trimpath -ldflags="-s -w" -o "$OUT/$name" .
}

# Linux: x86 (386), x64 (amd64), ARMv6, ARMv7, ARM64
build linux 386   ""  o2a-tls-linux-386
build linux amd64 ""  o2a-tls-linux-amd64
build linux arm   6   o2a-tls-linux-armv6
build linux arm   7   o2a-tls-linux-armv7
build linux arm64 ""  o2a-tls-linux-arm64

# macOS: Intel x64 and Apple Silicon
build darwin amd64 "" o2a-tls-darwin-amd64
build darwin arm64 "" o2a-tls-darwin-arm64

# Windows: x86, x64, ARM64
build windows 386   "" o2a-tls-windows-386.exe
build windows amd64 "" o2a-tls-windows-amd64.exe
build windows arm64 "" o2a-tls-windows-arm64.exe

# FreeBSD: x86, x64, ARM64
build freebsd 386   "" o2a-tls-freebsd-386
build freebsd amd64 "" o2a-tls-freebsd-amd64
build freebsd arm64 "" o2a-tls-freebsd-arm64

# Android: arm32, arm64, x86, x64 (real GOOS=android builds)
if [ -z "${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}" ]; then
  sdk="${ANDROID_HOME:-/opt/android-sdk}"
  if [ -d "$sdk/ndk" ]; then
    ANDROID_NDK_HOME="$(find "$sdk/ndk" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -1)"
  fi
fi
NDK="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}"
if [ -n "$NDK" ] && [ -x "$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/clang" ]; then
  build_android arm   armv7a-linux-androideabi24-clang o2a-tls-android-armv7
  build_android arm64 aarch64-linux-android24-clang     o2a-tls-android-arm64
  build_android 386   i686-linux-android24-clang        o2a-tls-android-x86
  build_android amd64 x86_64-linux-android24-clang      o2a-tls-android-x64
else
  echo "skip Android targets: set ANDROID_NDK_HOME to an NDK with clang" >&2
fi

(cd "$OUT" && sha256sum o2a-tls-* > SHA256SUMS.txt)
echo "--- output ---"
ls -lh "$OUT"
