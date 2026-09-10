"""
Robust model downloader for Whisper weights.

Why this exists: `huggingface.co` is unreachable from mainland China, and the
common `hf-mirror.com` mirror measured ~77 KB/s here (≈6 h for a 1.6 GB model).
ModelScope serves the identical MLX repo an order of magnitude faster, so we try
sources in order of measured throughput and download ranges in parallel with
resume, which lifts aggregate speed well past any single-connection cap.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import config

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 media-transcriber"

DEFAULT_MODELS_DIR = config.MODELS_DIR


@dataclass
class Source:
    name: str
    file_url: Callable[[str, str], str]


def _modelscope_url(repo: str, filename: str) -> str:
    return (
        f"https://modelscope.cn/api/v1/models/{repo}/repo"
        f"?Revision=master&FilePath={filename}"
    )


def _hf_mirror_url(repo: str, filename: str) -> str:
    return f"https://hf-mirror.com/{repo}/resolve/main/{filename}"


def _hf_url(repo: str, filename: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/main/{filename}"


SOURCES = [
    Source("modelscope", _modelscope_url),
    Source("hf-mirror", _hf_mirror_url),
    Source("huggingface", _hf_url),
]

# MLX Whisper repos only need these two files.
MLX_FILES = ["config.json", "weights.safetensors"]


def mlx_model_ready(repo: str, models_dir: Path = DEFAULT_MODELS_DIR) -> bool:
    """True when turbo weights are already on disk (cache or bench copy)."""
    target = models_dir / repo.replace("/", "--")
    short = repo.rsplit("/", 1)[-1]
    bench = Path(__file__).resolve().parent.parent / "bench" / "models" / short
    for candidate in (target, bench):
        if all((candidate / f).exists() and (candidate / f).stat().st_size > 0 for f in MLX_FILES):
            return True
    return False


def _head_size(url: str, timeout: float = 20.0) -> Optional[int]:
    """Return content length, using a ranged GET (HEAD is unreliable on both hubs)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cr = r.headers.get("Content-Range")
            if cr and "/" in cr:
                total = cr.rsplit("/", 1)[-1].strip()
                if total.isdigit():
                    return int(total)
            cl = r.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > 1:
                return int(cl)
    except Exception as e:
        logger.debug(f"size probe failed for {url}: {e}")
    return None


def _download_range(url: str, start: int, end: int, dest: Path, timeout: float,
                    counter: list, lock: threading.Lock, stop: threading.Event,
                    attempts: int = 12) -> None:
    """
    Download byte range [start, end] into `dest`, resuming from whatever is
    already there.

    Long transfers over the China mirrors drop connections routinely — a 1.6 GB
    model reliably loses a socket or two on the way. Each range therefore
    reconnects and continues from its own offset instead of failing the whole
    download.
    """
    total_needed = end - start + 1
    last_err: Optional[Exception] = None

    for attempt in range(attempts):
        have = dest.stat().st_size if dest.exists() else 0
        if have >= total_needed:
            return
        if stop.is_set():
            return

        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Range": f"bytes={start + have}-{end}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "ab") as f:
                while not stop.is_set():
                    chunk = r.read(1 << 18)
                    if not chunk:
                        break
                    f.write(chunk)
                    with lock:
                        counter[0] += len(chunk)
            if (dest.stat().st_size if dest.exists() else 0) >= total_needed:
                return
            last_err = IOError("connection closed before range completed")
        except Exception as e:
            last_err = e

        time.sleep(min(2 ** min(attempt, 5), 20))

    if not stop.is_set():
        raise IOError(f"range {start}-{end} failed after {attempts} attempts: {last_err}")


def _parallel_download(url: str, out: Path, total: int, connections: int,
                       progress: Optional[Callable[[int, int], None]],
                       timeout: float = 60.0,
                       cancel: Optional[threading.Event] = None) -> None:
    """Split into `connections` ranges, fetch concurrently, then concatenate."""
    part_dir = out.parent / f".{out.name}.parts"
    part_dir.mkdir(parents=True, exist_ok=True)

    span = total // connections
    ranges = []
    for i in range(connections):
        start = i * span
        end = total - 1 if i == connections - 1 else (start + span - 1)
        ranges.append((start, end, part_dir / f"part{i:03d}"))

    already = sum(p.stat().st_size if p.exists() else 0 for *_, p in ranges)
    counter = [already]
    lock = threading.Lock()
    stop = threading.Event()
    errors: list = []

    def worker(start, end, dest):
        try:
            _download_range(url, start, end, dest, timeout, counter, lock, stop)
        except Exception as e:
            errors.append(e)
            stop.set()

    threads = [threading.Thread(target=worker, args=r, daemon=True) for r in ranges]
    for t in threads:
        t.start()

    last_report = 0.0
    while any(t.is_alive() for t in threads):
        if cancel and cancel.is_set():
            stop.set()
            break
        time.sleep(0.5)
        if progress and time.time() - last_report > 0.4:
            progress(min(counter[0], total), total)
            last_report = time.time()
    for t in threads:
        t.join()
    if cancel and cancel.is_set():
        raise InterruptedError("cancelled")

    if errors:
        raise errors[0]

    got = sum(p.stat().st_size for _, _, p in ranges if p.exists())
    if got < total:
        raise IOError(f"incomplete download: {got}/{total} bytes")

    tmp = out.parent / f".{out.name}.assembling"
    with open(tmp, "wb") as dst:
        for _, _, p in ranges:
            with open(p, "rb") as src:
                shutil.copyfileobj(src, dst, 1 << 20)
    tmp.replace(out)
    shutil.rmtree(part_dir, ignore_errors=True)
    if progress:
        progress(total, total)


def ensure_mlx_model(
    repo: str,
    models_dir: Path = DEFAULT_MODELS_DIR,
    connections: int = 8,
    progress: Optional[Callable[[str, int, int], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> Path:
    """
    Ensure an MLX Whisper repo is present locally; download it if not.

    Returns the local directory, suitable for `path_or_hf_repo=`.
    `progress(filename, done_bytes, total_bytes)` is called during transfer.
    """
    target = models_dir / repo.replace("/", "--")
    target.mkdir(parents=True, exist_ok=True)

    short = repo.rsplit("/", 1)[-1]
    for candidate in (
        target,
        Path(__file__).resolve().parent.parent / "bench" / "models" / short,
    ):
        if all((candidate / f).exists() and (candidate / f).stat().st_size > 0 for f in MLX_FILES):
            if candidate != target:
                target.mkdir(parents=True, exist_ok=True)
                for f in MLX_FILES:
                    dest = target / f
                    src = candidate / f
                    if dest.exists() and dest.stat().st_size > 0:
                        continue
                    try:
                        os.link(src, dest)
                    except OSError:
                        shutil.copy2(src, dest)
            return target

    last_err: Optional[Exception] = None
    for source in SOURCES:
        try:
            for filename in MLX_FILES:
                out = target / filename
                if out.exists() and out.stat().st_size > 0:
                    continue
                url = source.file_url(repo, filename)
                size = _head_size(url)
                if size is None:
                    raise IOError(f"cannot determine size of {filename} via {source.name}")

                logger.info(
                    f"downloading {filename} ({size / 1e6:.0f} MB) "
                    f"from {source.name} with {connections} connections"
                )

                def cb(done, tot, _f=filename):
                    if progress:
                        progress(_f, done, tot)

                if size < 4 << 20:
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        out.write_bytes(r.read())
                    cb(size, size)
                else:
                    _parallel_download(url, out, size, connections, cb, cancel=cancel)
                if cancel and cancel.is_set():
                    raise InterruptedError("cancelled")

            logger.info(f"model ready: {target}")
            return target
        except InterruptedError:
            raise
        except Exception as e:
            last_err = e
            logger.warning(f"source {source.name} failed: {e}")

    raise RuntimeError(
        f"无法下载模型 {repo}。已尝试 ModelScope / hf-mirror / HuggingFace 均失败。"
        f"最后一个错误：{last_err}"
    )


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    repo = sys.argv[1] if len(sys.argv) > 1 else "mlx-community/whisper-large-v3-turbo"
    t0 = time.time()
    started = {"t": time.time(), "b": 0}

    def show(fn, done, total):
        el = max(time.time() - started["t"], 0.1)
        pct = 100.0 * done / total if total else 0
        print(f"\r{fn}: {pct:5.1f}%  {done/1e6:7.1f}/{total/1e6:.0f} MB  "
              f"{done/el/1e6:5.2f} MB/s", end="", flush=True)

    path = ensure_mlx_model(repo, progress=show)
    print(f"\ndone in {time.time() - t0:.0f}s -> {path}")
