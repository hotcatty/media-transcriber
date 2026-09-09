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
UV_ASSET="uv-aarch64-apple-darwin.tar.gz"
[[ "$ARCH" == "x86_64" ]] && UV_ASSET="uv-x86_64-apple-darwin.tar.gz"
UV_TGZ="$DIST/uv.tgz"
echo "download $UV_ASSET"
curl -fL --retry 3 --retry-delay 2 -o "$UV_TGZ" \
  "https://github.com/astral-sh/uv/releases/latest/download/${UV_ASSET}"
tar -xzf "$UV_TGZ" -C "$APP/Contents/Resources/bin"
# tarball may nest the binary
if [[ ! -x "$APP/Contents/Resources/bin/uv" ]]; then
  found="$(find "$APP/Contents/Resources/bin" -name uv -type f | head -1)"
  [[ -n "$found" ]] && mv "$found" "$APP/Contents/Resources/bin/uv"
fi
chmod +x "$APP/Contents/Resources/bin/uv"
rm -f "$UV_TGZ"
# drop extra files from the uv tarball
find "$APP/Contents/Resources/bin" -mindepth 1 -maxdepth 1 ! -name uv -exec rm -rf {} +

(
  cd "$STAGE"
  rm -f "$ZIP"
  zip -r -y "$ZIP" "转录小工具.app"
)

echo "wrote $ZIP"
ls -lh "$ZIP"
