#!/bin/bash
# Build the downloadable Mac helper zip for GitHub Releases.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/packaging/macos/Transcriber.app"
DIST="$ROOT/packaging/macos/dist"
STAGE="$DIST/stage"
APP="$STAGE/转录小工具.app"
ZIP="$DIST/MediaTranscriber-macOS.zip"

rm -rf "$STAGE" "$ZIP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app" "$APP/Contents/Resources/bin"

rsync -a "$SRC/Contents/Info.plist" "$APP/Contents/"
rsync -a "$SRC/Contents/MacOS/launcher" "$APP/Contents/MacOS/"
rsync -a "$SRC/Contents/Resources/waiting.html" "$APP/Contents/Resources/"
chmod +x "$APP/Contents/MacOS/launcher"

APP_RES="$APP/Contents/Resources/app"
mkdir -p "$APP_RES"
cp "$ROOT/start.py" "$ROOT/requirements.txt" "$ROOT/LICENSE" "$ROOT/README.md" "$APP_RES/"
rsync -a --delete --exclude __pycache__ --exclude temp --exclude .venv \
  "$ROOT/backend/" "$APP_RES/backend/"
rsync -a --delete --exclude __pycache__ \
  --exclude _dl --exclude .DS_Store \
  "$ROOT/static/" "$APP_RES/static/"

ARCH="$(uname -m)"
BIN="$APP/Contents/Resources/bin"

fetch_uv() {
  local asset="$1"
  local name="$2"
  local tgz="$DIST/${name}.tgz"
  local unpack="$DIST/uv-unpack-${name}"
  rm -rf "$unpack"
  mkdir -p "$unpack"
  echo "download $asset -> $name"
  curl -fL --retry 3 --retry-delay 2 -o "$tgz" \
    "https://github.com/astral-sh/uv/releases/latest/download/${asset}"
  tar -xzf "$tgz" -C "$unpack"
  local found
  found="$(find "$unpack" -name uv -type f | head -1)"
  cp "$found" "$BIN/$name"
  chmod +x "$BIN/$name"
  rm -rf "$tgz" "$unpack"
}

fetch_uv uv-aarch64-apple-darwin.tar.gz uv-arm64
fetch_uv uv-x86_64-apple-darwin.tar.gz uv-x86_64
if [[ "$ARCH" == "x86_64" ]]; then
  cp "$BIN/uv-x86_64" "$BIN/uv"
else
  cp "$BIN/uv-arm64" "$BIN/uv"
fi
chmod +x "$BIN/uv"

# Bundle CPython so first launch does not need GitHub to install Python.
PY_DIR="$APP/Contents/Resources/python"
mkdir -p "$PY_DIR"
export UV_PYTHON_INSTALL_DIR="$PY_DIR"
export UV_PYTHON_INSTALL_MIRROR="${UV_PYTHON_INSTALL_MIRROR:-https://cdn.npmmirror.com/binaries/python-build-standalone}"
echo "install python 3.12 into bundle (host + both Mac archs if possible)"
if ! "$APP/Contents/Resources/bin/uv" python install 3.12; then
  echo "warning: could not bundle host python; runtime will try download" >&2
fi
"$APP/Contents/Resources/bin/uv" python install cpython-3.12-macos-aarch64-none || true
"$APP/Contents/Resources/bin/uv" python install cpython-3.12-macos-x86_64-none || true
if [[ -z "$(find "$PY_DIR" -name python3.12 -type f 2>/dev/null | head -1)" ]]; then
  echo "warning: python bundle empty; runtime will try download" >&2
  rm -rf "$PY_DIR"
fi

(
  cd "$STAGE"
  rm -f "$ZIP"
  zip -r -y "$ZIP" "转录小工具.app"
)

echo "wrote $ZIP"
ls -lh "$ZIP"
