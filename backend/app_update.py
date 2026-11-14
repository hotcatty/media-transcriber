"""Background update for the local Mac/Windows helper.

The window must already be usable before this runs. A slow download updates
the status line; it does not close the app or report that the app failed to open.
There is no overall download deadline.
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
import zipfile
from pathlib import Path
from typing import Callable, Optional

import config

logger = logging.getLogger(__name__)

REPO = "hotcatty/media-transcriber"
# A slow proxy seen in the field. Replaced by the measured speed once bytes arrive.
PRIOR_BPS = 40 * 1024
# Used only until the response tells us the real size.
PRIOR_ZIP_BYTES = 8 * 1024 * 1024
# Production downloads do not abort after a fixed number of seconds.
OVERALL_DOWNLOAD_DEADLINE = None

_OPENER = Callable[..., object]

_manager: Optional["UpdateManager"] = None
_manager_lock = threading.Lock()


def auto_update_enabled() -> bool:
    return os.getenv("MT_AUTO_UPDATE", "") == "1"


def data_root() -> Path:
    temp = os.getenv("MT_TEMP_DIR")
    if temp:
        return Path(temp).expanduser().resolve().parent
    return Path.home() / "Library" / "Application Support" / "media-transcriber"


def version_parts(tag: str) -> tuple:
    out = []
    for part in str(tag or "").strip().lstrip("v").split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out or (0,))


def version_newer(latest: str, current: str) -> bool:
    return version_parts(latest) > version_parts(current)


def format_eta(seconds: Optional[float]) -> str:
    """Chinese duration for the update banner."""
    if seconds is None:
        return "大约还需几分钟"
    s = int(round(max(0.0, float(seconds))))
    if s < 90:
        return f"大约还需{max(1, s)}秒"
    minutes = max(1, int(round(s / 60.0)))
    return f"大约还需{minutes}分钟"


def estimate_remaining(
    received: int,
    total: Optional[int],
    elapsed: float,
    prior_bps: float = PRIOR_BPS,
) -> Optional[float]:
    """Seconds still expected. Measured speed wins after a small sample.

    Before that, assume the slow-link prior so the banner can show a number
    immediately. A large result is a duration, not a reason to give up.
    """
    got = max(int(received or 0), 0)
    if total is not None and int(total) > 0:
        expected = int(total)
    else:
        expected = max(PRIOR_ZIP_BYTES, got + 256 * 1024)
    if got > expected:
        expected = got + 256 * 1024
    remain = max(0, expected - got)
    if got >= 32 * 1024 and elapsed >= 0.4:
        speed = got / elapsed
        if speed > 0:
            return remain / speed
    if prior_bps <= 0:
        return None
    return remain / prior_bps


def progress_message(phase: str, eta_seconds: Optional[float] = None) -> str:
    if phase == "downloading":
        return f"正在更新，{format_eta(eta_seconds)}。可以继续使用。"
    if phase == "applying":
        return f"正在换上新版本，{format_eta(eta_seconds)}。"
    if phase == "staged":
        return "更新已下载，下次打开会自动换上。现在可以继续使用。"
    if phase == "failed":
        return "这次没能下完更新，当前版本可以继续使用。"
    return ""


def idle_payload(current: str) -> dict:
    return {
        "current": current,
        "latest": current,
        "newer": False,
        "url": "",
        "phase": "idle",
        "message": "",
        "eta_seconds": None,
        "received": 0,
        "total": None,
    }


def zip_urls(tag: str) -> list[str]:
    raw = str(tag or "").strip().lstrip("v")
    names: list[str] = []
    if raw:
        names.append("v" + raw)
        names.append(raw)
    out: list[str] = []
    seen = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        base = f"https://codeload.github.com/{REPO}/zip/refs/tags/{name}"
        out.append(base)
        out.append(f"https://ghfast.top/{base}")
        out.append(f"https://ghproxy.net/{base}")
    return out


def content_length(resp: object) -> Optional[int]:
    headers = getattr(resp, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return None
    raw = headers.get("Content-Length")
    if raw is None:
        raw = headers.get("content-length")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for info in zf.infolist():
        name = info.filename
        parts = Path(name).parts
        if name.startswith(("/", "\\")) or ".." in parts:
            raise RuntimeError("unsafe path in update archive")
        target = (root / name).resolve()
        if target != root and root not in target.parents:
            raise RuntimeError("unsafe path in update archive")
    zf.extractall(root)


def find_app_tree(root: Path) -> Path:
    if (root / "backend").is_dir():
        return root
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "backend").is_dir():
            return child
    raise RuntimeError("update archive has no backend")


class UpdateManager:
    def __init__(self, root: Path, current: str, opener: Optional[_OPENER] = None):
        self.root = Path(root)
        self.current = current
        self._opener = opener
        self._lock = threading.Lock()
        self._started = False
        self._state = {
            "phase": "idle",
            "latest": current,
            "newer": False,
            "url": "",
            "message": "",
            "eta_seconds": None,
            "received": 0,
            "total": None,
        }

    def snapshot(self) -> dict:
        with self._lock:
            state = dict(self._state)
        eta = state.get("eta_seconds")
        if isinstance(eta, float):
            eta = round(eta, 1)
        latest = state.get("latest") or self.current
        return {
            "current": self.current,
            "latest": latest,
            "newer": bool(state.get("newer")),
            "url": state.get("url") or "",
            "phase": state.get("phase") or "idle",
            "message": state.get("message") or "",
            "eta_seconds": eta,
            "received": int(state.get("received") or 0),
            "total": state.get("total"),
        }

    def ensure_started(self) -> dict:
        start = False
        with self._lock:
            if not self._started:
                self._started = True
                self._state["phase"] = "checking"
                start = True
        if start:
            threading.Thread(target=self.run, name="mt-update", daemon=True).start()
        return self.snapshot()

    def _set(self, **fields) -> None:
        with self._lock:
            self._state.update(fields)

    def _open(self, req: urllib.request.Request, timeout: float):
        if self._opener is not None:
            return self._opener(req, timeout)
        return urllib.request.urlopen(req, timeout=timeout)

    def run(self) -> None:
        """Check, then download when a newer release exists. Never raises to the UI."""
        try:
            latest, page = self.fetch_latest()
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
            logger.info("update check skipped: %s", exc)
            self._set(phase="idle", newer=False, message="", eta_seconds=None)
            return
        newer = version_newer(latest, self.current)
        if not newer:
            self._set(
                phase="idle",
                latest=latest,
                newer=False,
                url="",
                message="",
                eta_seconds=None,
            )
            return
        ready = self.root / "pending-update" / "READY"
        tree = self.root / "pending-update" / "tree" / "backend"
        if ready.is_file() and tree.is_dir():
            have = ready.read_text(encoding="utf-8").strip().lstrip("v")
            if have == latest:
                self._set(
                    phase="staged",
                    latest=latest,
                    newer=True,
                    url=page,
                    message=progress_message("staged"),
                    eta_seconds=None,
                )
                return
        self._set(
            phase="downloading",
            latest=latest,
            newer=True,
            url=page,
            message=progress_message("downloading", None),
            eta_seconds=None,
            received=0,
            total=None,
        )
        try:
            self.download_and_stage(latest, page)
        except Exception as exc:
            logger.info("update download failed: %s", exc)
            self._set(
                phase="failed",
                latest=latest,
                newer=True,
                url=page,
                message=progress_message("failed"),
                eta_seconds=None,
            )

    def fetch_latest(self) -> tuple[str, str]:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={
                "User-Agent": "media-transcriber",
                "Accept": "application/vnd.github+json",
            },
        )
        with self._open(req, 15) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        tag = str(data.get("tag_name") or "").strip().lstrip("v")
        if not tag:
            raise RuntimeError("missing tag")
        return tag, config.UPDATE_URL

    def download_and_stage(self, latest: str, page: str) -> None:
        pending = self.root / "pending-update"
        pending.mkdir(parents=True, exist_ok=True)
        partial = pending / "src.zip.partial"
        last: Optional[BaseException] = None
        for url in zip_urls(latest):
            try:
                self._download_one(url, partial)
                last = None
                break
            except Exception as exc:
                last = exc
                partial.unlink(missing_ok=True)
                logger.info("update url failed: %s", exc)
        if last is not None or not partial.is_file():
            raise last or RuntimeError("update download failed")
        self._stage_zip(partial, latest, page)

    def _download_one(self, url: str, dest: Path) -> None:
        """Stream one URL. A slow steady transfer is not aborted."""
        req = urllib.request.Request(url, headers={"User-Agent": "media-transcriber"})
        started = time.monotonic()
        received = 0
        # Socket timeout only bounds a stalled read. OVERALL_DOWNLOAD_DEADLINE
        # stays None so a slow link cannot be reported as a failed launch.
        if OVERALL_DOWNLOAD_DEADLINE is not None:
            raise RuntimeError("update downloads must not use an overall deadline")
        with self._open(req, 120) as resp:
            total = content_length(resp)
            eta = estimate_remaining(0, total, 0.0)
            self._set(
                phase="downloading",
                received=0,
                total=total,
                eta_seconds=eta,
                message=progress_message("downloading", eta),
            )
            with dest.open("wb") as fh:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    received += len(chunk)
                    elapsed = time.monotonic() - started
                    eta = estimate_remaining(received, total, elapsed)
                    self._set(
                        phase="downloading",
                        received=received,
                        total=total,
                        eta_seconds=eta,
                        message=progress_message("downloading", eta),
                    )
        if received <= 0:
            raise RuntimeError("empty update download")

    def _stage_zip(self, zip_path: Path, latest: str, page: str) -> None:
        pending = self.root / "pending-update"
        unpack = pending / "unpack"
        if unpack.exists():
            shutil.rmtree(unpack)
        unpack.mkdir(parents=True)
        with zipfile.ZipFile(zip_path) as zf:
            safe_extract(zf, unpack)
        src = find_app_tree(unpack)
        tree = pending / "tree"
        if tree.exists():
            shutil.rmtree(tree)
        shutil.move(str(src), str(tree))
        if unpack.exists():
            shutil.rmtree(unpack, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
        (pending / "READY").write_text(latest.strip().lstrip("v") + "\n", encoding="utf-8")
        self._set(
            phase="staged",
            latest=latest.strip().lstrip("v"),
            newer=True,
            url=page,
            message=progress_message("staged"),
            eta_seconds=None,
        )


def ensure_background(current: str) -> dict:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = UpdateManager(data_root(), current)
        manager = _manager
    return manager.ensure_started()


def reset_for_tests() -> None:
    global _manager
    with _manager_lock:
        _manager = None
