#!/bin/bash
# Build the downloadable Mac helper zip for GitHub Releases.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/packaging/macos/Transcriber.app"
DIST="$ROOT/packaging/macos/dist"
STAGE="$DIST/stage"
APP="$STAGE/猫听转文字.app"
ZIP="$DIST/MediaTranscriber-macOS.zip"

rm -rf "$STAGE" "$ZIP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app" "$APP/Contents/Resources/bin"

rsync -a "$SRC/Contents/Info.plist" "$APP/Contents/"
rsync -a "$SRC/Contents/MacOS/launcher" "$APP/Contents/MacOS/launcher.bash"
rsync -a "$SRC/Contents/Resources/waiting.html" "$APP/Contents/Resources/"
rsync -a "$ROOT/packaging/icons/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
rsync -a "$ROOT/desktop_splash.py" "$APP/Contents/Resources/"
clang -arch arm64 -arch x86_64 -mmacosx-version-min=13.0 -Os \
  -o "$APP/Contents/MacOS/launcher" "$ROOT/packaging/macos/stub.c"
chmod +x "$APP/Contents/MacOS/launcher" "$APP/Contents/MacOS/launcher.bash"

APP_RES="$APP/Contents/Resources/app"
mkdir -p "$APP_RES"
cp "$ROOT/start.py" "$ROOT/desktop_window.py" "$ROOT/desktop_splash.py" "$ROOT/requirements.txt" "$ROOT/LICENSE" "$ROOT/README.md" "$APP_RES/"
cp "$ROOT/packaging/icons/app-icon.png" "$APP_RES/"
rsync -a --delete --exclude __pycache__ --exclude temp --exclude .venv \
  "$ROOT/backend/" "$APP_RES/backend/"
rsync -a --delete --exclude __pycache__ \
  --exclude _dl --exclude .DS_Store --exclude aurora.js \
  "$ROOT/static/" "$APP_RES/static/"

ARCH="$(uname -m)"
BIN="$APP/Contents/Resources/bin"
LOCAL_APP="${HOME}/Applications/猫听转文字.app"
LOCAL_BIN=""
for cand in \
  "$LOCAL_APP/Contents/Resources/bin" \
  "$HOME/Library/Application Support/media-transcriber/bin"
do
  if [[ -x "$cand/uv-arm64" && -x "$cand/uv-x86_64" ]]; then
    LOCAL_BIN="$cand"
    break
  fi
done

fetch_uv() {
  local asset="$1"
  local name="$2"
  local tgz="$DIST/${name}.tgz"
  local unpack="$DIST/uv-unpack-${name}"
  rm -rf "$unpack"
  mkdir -p "$unpack"
  echo "download $asset -> $name"
  if [[ ! -s "$tgz" ]]; then
    curl -fL --retry 5 --retry-all-errors --retry-delay 2 -o "$tgz" \
      "https://github.com/astral-sh/uv/releases/latest/download/${asset}"
  fi
  tar -xzf "$tgz" -C "$unpack"
  local found
  found="$(find "$unpack" -name uv -type f | head -1)"
  cp "$found" "$BIN/$name"
  chmod +x "$BIN/$name"
  rm -rf "$tgz" "$unpack"
}

if [[ -n "$LOCAL_BIN" ]]; then
  echo "reuse local uv from $LOCAL_BIN"
  cp "$LOCAL_BIN/uv-arm64" "$BIN/uv-arm64"
  cp "$LOCAL_BIN/uv-x86_64" "$BIN/uv-x86_64"
  chmod +x "$BIN/uv-arm64" "$BIN/uv-x86_64"
else
  fetch_uv uv-aarch64-apple-darwin.tar.gz uv-arm64
  fetch_uv uv-x86_64-apple-darwin.tar.gz uv-x86_64
fi
if [[ "$ARCH" == "x86_64" ]]; then
  cp "$BIN/uv-x86_64" "$BIN/uv"
else
  cp "$BIN/uv-arm64" "$BIN/uv"
fi
chmod +x "$BIN/uv"

# Bundle CPython so first launch does not need GitHub to install Python.
PY_DIR="$APP/Contents/Resources/python"
mkdir -p "$PY_DIR"
LOCAL_PY=""
for cand in \
  "$LOCAL_APP/Contents/Resources/python" \
  "$HOME/Library/Application Support/media-transcriber/python"
do
  if [[ -n "$(find "$cand" -name python3.12 -type f 2>/dev/null | head -1)" ]]; then
    LOCAL_PY="$cand"
    break
  fi
done
if [[ -n "$LOCAL_PY" ]]; then
  echo "reuse local python from $LOCAL_PY"
  rsync -a "$LOCAL_PY/" "$PY_DIR/"
else
  export UV_PYTHON_INSTALL_DIR="$PY_DIR"
  export UV_PYTHON_INSTALL_MIRROR="${UV_PYTHON_INSTALL_MIRROR:-https://cdn.npmmirror.com/binaries/python-build-standalone}"
  echo "install python 3.12 into bundle (host + both Mac archs if possible)"
  if ! "$APP/Contents/Resources/bin/uv" python install 3.12; then
    echo "warning: could not bundle host python; runtime will try download" >&2
  fi
  "$APP/Contents/Resources/bin/uv" python install cpython-3.12-macos-aarch64-none || true
  "$APP/Contents/Resources/bin/uv" python install cpython-3.12-macos-x86_64-none || true
fi
if [[ -z "$(find "$PY_DIR" -name python3.12 -type f 2>/dev/null | head -1)" ]]; then
  echo "warning: python bundle empty; runtime will try download" >&2
  rm -rf "$PY_DIR"
fi

(
  cd "$STAGE"
  rm -f "$ZIP"
  zip -r -y "$ZIP" "猫听转文字.app"
)

echo "wrote $ZIP"
ls -lh "$ZIP"
