#!/usr/bin/env bash
# Bootstrap venv + ffmpeg, then transcribe a URL or local file.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    PY="$c"
    break
  fi
done
if [[ -z "$PY" ]]; then
  echo "需要 Python 3.10+。macOS：brew install python；Windows：从 python.org 安装。" >&2
  exit 1
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "正在创建虚拟环境…" >&2
  "$PY" -m venv "$ROOT/.venv"
fi
VPY="$ROOT/.venv/bin/python"

if ! "$VPY" -c "import fastapi, yt_dlp" 2>/dev/null; then
  echo "正在安装依赖（只需这一次）…" >&2
  "$VPY" -m pip install -q -U pip
  "$VPY" -m pip install -q -r "$ROOT/requirements.txt"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "未检测到 ffmpeg。macOS：brew install ffmpeg；Debian/Ubuntu：sudo apt install ffmpeg；Windows 可用 scoop/choco 安装 ffmpeg。" >&2
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "用法：bash scripts/run.sh \"<链接或本地文件>\" [--out 目录]" >&2
  exit 1
fi

exec "$VPY" "$ROOT/scripts/transcribe.py" "$@"
