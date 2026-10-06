#!/bin/bash
# Build the downloadable Windows helper zip for GitHub Releases.
# Can run on macOS: downloads the Windows uv/ffmpeg binaries into the zip.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/packaging/windows/dist"
STAGE="$DIST/stage"
APP="$STAGE/media-transcriber"
ZIP="$DIST/MediaTranscriber-windows.zip"

rm -rf "$STAGE" "$ZIP"
mkdir -p "$APP/app" "$APP/bin"

cp "$ROOT/packaging/windows/start.bat" "$APP/"
cp "$ROOT/packaging/windows/launcher.ps1" "$APP/"
cp "$ROOT/packaging/windows/app.ico" "$APP/"
cp "$ROOT/packaging/macos/Transcriber.app/Contents/Resources/waiting.html" "$APP/"
cp "$ROOT/desktop_splash.py" "$APP/"
cp "$ROOT/start.py" "$ROOT/desktop_window.py" "$ROOT/desktop_splash.py" "$ROOT/requirements.txt" "$ROOT/LICENSE" "$ROOT/README.md" "$APP/app/"
cp "$ROOT/packaging/icons/app-icon.png" "$APP/app/"
rsync -a --delete --exclude __pycache__ --exclude temp --exclude .venv \
  "$ROOT/backend/" "$APP/app/backend/"
rsync -a --delete --exclude __pycache__ \
  --exclude _dl --exclude .DS_Store --exclude aurora.js \
  "$ROOT/static/" "$APP/app/static/"

UV_ZIP="$DIST/uv-win.zip"
echo "download uv-x86_64-pc-windows-msvc.zip"
if [[ ! -s "$UV_ZIP" ]]; then
  curl -fL --retry 5 --retry-all-errors --retry-delay 2 -o "$UV_ZIP" \
    "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
fi
unzip -qo "$UV_ZIP" -d "$DIST/uv-unpack"
found="$(find "$DIST/uv-unpack" -name uv.exe -type f | head -1)"
[[ -n "$found" ]]
cp "$found" "$APP/bin/uv.exe"
rm -rf "$DIST/uv-unpack"

TAG="b6.1.1"
echo "download windows ffmpeg"
if [[ ! -s "$DIST/ffmpeg.gz" ]]; then
  curl -fL --retry 5 --retry-all-errors --retry-delay 2 -o "$DIST/ffmpeg.gz" \
    "https://github.com/eugeneware/ffmpeg-static/releases/download/${TAG}/ffmpeg-win32-x64.gz" \
    || echo "warning: could not download ffmpeg; runtime will fetch it" >&2
fi
if [[ ! -s "$DIST/ffprobe.gz" ]]; then
  curl -fL --retry 5 --retry-all-errors --retry-delay 2 -o "$DIST/ffprobe.gz" \
    "https://github.com/eugeneware/ffmpeg-static/releases/download/${TAG}/ffprobe-win32-x64.gz" \
    || echo "warning: could not download ffprobe; runtime will fetch it" >&2
fi
python3 - "$DIST" "$APP/bin" <<'PY'
import gzip, shutil, sys
from pathlib import Path
dist = Path(sys.argv[1])
bin_dir = Path(sys.argv[2])
missing = []
for name in ("ffmpeg", "ffprobe"):
    src = dist / f"{name}.gz"
    dest = bin_dir / f"{name}.exe"
    if not src.exists() or src.stat().st_size == 0:
        missing.append(name)
        continue
    with gzip.open(src, "rb") as f_in, open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
if missing:
    print("warning: skip bundling " + ", ".join(missing) + "; runtime will download")
else:
    print("wrote ffmpeg/ffprobe")
PY

UV_BIN="$(command -v uv 2>/dev/null || true)"
if [[ -z "${UV_BIN}" && -x /Users/hotcat/.local/bin/uv ]]; then
  UV_BIN=/Users/hotcat/.local/bin/uv
fi
if [[ -z "${UV_BIN}" && -x "$ROOT/packaging/macos/dist/stage/猫听转文字.app/Contents/Resources/bin/uv" ]]; then
  UV_BIN="$ROOT/packaging/macos/dist/stage/猫听转文字.app/Contents/Resources/bin/uv"
fi
if [[ -n "${UV_BIN}" ]]; then
  export UV_PYTHON_INSTALL_DIR="$APP/python"
  export UV_PYTHON_INSTALL_MIRROR="${UV_PYTHON_INSTALL_MIRROR:-https://cdn.npmmirror.com/binaries/python-build-standalone}"
  mkdir -p "$UV_PYTHON_INSTALL_DIR"
  echo "try bundling windows cpython (best-effort)"
  if ! "$UV_BIN" python install cpython-3.12-windows-x86_64-none; then
    echo "warning: could not bundle Windows python; runtime will use the mirror" >&2
    rm -rf "$UV_PYTHON_INSTALL_DIR"
  elif [[ -z "$(find "$UV_PYTHON_INSTALL_DIR" -type f 2>/dev/null | head -1)" ]]; then
    rm -rf "$UV_PYTHON_INSTALL_DIR"
  fi
fi

(
  cd "$STAGE"
  rm -f "$ZIP"
  zip -r "$ZIP" "media-transcriber"
)

echo "wrote $ZIP"
ls -lh "$ZIP"
