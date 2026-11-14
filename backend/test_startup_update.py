"""Startup no longer waits on GitHub, and a slow update stays visible."""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

import app_update
import desktop_window

LAUNCHER = ROOT / "packaging/macos/Transcriber.app/Contents/MacOS/launcher"
WINDOWS = ROOT / "packaging/windows/launcher.ps1"


class _Body:
    def __init__(self, body: bytes, headers=None, chunk: int = 0, delay: float = 0):
        self._body = body
        self.headers = headers or {"Content-Length": str(len(body))}
        self._i = 0
        self._chunk = chunk or len(body) or 1
        self._delay = delay

    def read(self, n=-1):
        if self._delay:
            time.sleep(self._delay)
        if self._i >= len(self._body):
            return b""
        if n is None or n < 0:
            part = self._body[self._i:]
            self._i = len(self._body)
            return part
        take = min(n, self._chunk, len(self._body) - self._i)
        part = self._body[self._i:self._i + take]
        self._i += len(part)
        return part

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("media-transcriber-1.4.0/backend/config.py", 'APP_VERSION = "1.4.0"\n')
        zf.writestr("media-transcriber-1.4.0/start.py", "print('ok')\n")
    return buf.getvalue()


class UpdateTests(unittest.TestCase):
    def test_slow_link_estimate_is_a_duration_past_the_old_90s_cutoff(self):
        # 40 KB/s and two more minutes of bytes. The old window died at 90s.
        remain = app_update.estimate_remaining(40 * 1024, 40 * 1024 + 120 * 40 * 1024, 1.0)
        self.assertGreater(remain, 90)
        msg = app_update.progress_message("downloading", remain)
        self.assertIn("正在更新", msg)
        self.assertIn("大约还需", msg)
        self.assertIn("可以继续使用", msg)
        self.assertNotIn("打不开", msg)
        self.assertNotIn("本机服务没有起来", msg)

    def test_unknown_size_still_shows_an_expected_duration(self):
        remain = app_update.estimate_remaining(0, None, 0.0)
        self.assertIsNotNone(remain)
        self.assertGreater(remain, 60)
        msg = app_update.progress_message("downloading", remain)
        self.assertTrue(msg.startswith("正在更新，大约还需"))

    def test_messages_do_not_look_like_a_failed_open(self):
        for phase in ("downloading", "applying", "staged", "failed"):
            msg = app_update.progress_message(phase, 45)
            self.assertNotIn("打不开", msg)
            self.assertNotIn("本机服务没有起来", msg)

    def test_download_finishes_when_the_estimate_would_have_exceeded_90s(self):
        payload = _zip_bytes()
        calls = []

        def opener(req: Request, timeout):
            calls.append(req.full_url)
            if "releases/latest" in req.full_url:
                body = json.dumps({"tag_name": "v1.4.0"}).encode()
                return _Body(body, chunk=len(body))
            # Tiny body, but the banner math uses a multi-minute prior until bytes land.
            return _Body(payload, chunk=1024, delay=0.01)

        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        mgr = app_update.UpdateManager(tmp, "1.3.1", opener=opener)
        seen = []
        orig = mgr._set

        def wrapped(**fields):
            orig(**fields)
            if fields.get("phase") == "downloading" and fields.get("message"):
                seen.append((fields["message"], fields.get("eta_seconds")))

        mgr._set = wrapped
        mgr.run()
        snap = mgr.snapshot()
        self.assertEqual(snap["phase"], "staged")
        self.assertIn("可以继续使用", snap["message"])
        self.assertTrue(seen)
        self.assertTrue(all(msg.startswith("正在更新") for msg, _eta in seen))
        self.assertTrue(any(eta is not None and eta > 0 for _msg, eta in seen))
        ready = (tmp / "pending-update" / "READY").read_text(encoding="utf-8").strip()
        self.assertEqual(ready, "1.4.0")
        self.assertTrue((tmp / "pending-update" / "tree" / "backend" / "config.py").is_file())
        self.assertEqual(app_update.OVERALL_DOWNLOAD_DEADLINE, None)
        self.assertTrue(any("releases/latest" in url for url in calls))
        self.assertTrue(any("/zip/refs/tags/" in url for url in calls))
        # The first tag URL succeeded, so mirrors were not required.
        self.assertEqual(sum("/zip/" in url for url in calls), 1)

    def test_check_failure_leaves_the_app_usable(self):
        def opener(req, timeout):
            raise TimeoutError("slow proxy")

        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        mgr = app_update.UpdateManager(tmp, "1.3.1", opener=opener)
        mgr.run()
        snap = mgr.snapshot()
        self.assertEqual(snap["phase"], "idle")
        self.assertEqual(snap["message"], "")
        self.assertFalse(snap["newer"])

    def test_same_version_does_not_download(self):
        calls = []

        def opener(req: Request, timeout):
            calls.append(req.full_url)
            body = json.dumps({"tag_name": "v1.3.1"}).encode()
            return _Body(body)

        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        mgr = app_update.UpdateManager(tmp, "1.3.1", opener=opener)
        mgr.run()
        self.assertEqual(mgr.snapshot()["phase"], "idle")
        self.assertFalse(any("/zip/" in url for url in calls))

    def test_unsafe_archive_does_not_escape_and_stays_usable(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../evil.txt", "nope")
        bad = buf.getvalue()

        def opener(req: Request, timeout):
            if "releases/latest" in req.full_url:
                body = json.dumps({"tag_name": "v9.9.9"}).encode()
                return _Body(body)
            return _Body(bad)

        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        mgr = app_update.UpdateManager(tmp, "1.3.1", opener=opener)
        mgr.run()
        snap = mgr.snapshot()
        self.assertEqual(snap["phase"], "failed")
        self.assertIn("可以继续使用", snap["message"])
        self.assertFalse((tmp / "evil.txt").exists())
        self.assertFalse((tmp.parent / "evil.txt").exists())


class WindowTests(unittest.TestCase):
    def test_preparing_opens_without_waiting_out_the_old_timeout(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"ok":true,"app":"media-transcriber","preparing":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        os.environ["MT_HOST"] = "127.0.0.1"
        os.environ["MT_PORT"] = str(port)
        started = time.time()
        kind = desktop_window.wait_until_up("", seconds=90)
        elapsed = time.time() - started
        self.assertEqual(kind, "preparing")
        self.assertLess(elapsed, 3)

    def test_ready_ping(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"ok":true,"app":"media-transcriber","preparing":false}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        os.environ["MT_HOST"] = "127.0.0.1"
        os.environ["MT_PORT"] = str(port)
        self.assertEqual(desktop_window.wait_until_up("", seconds=5), "ready")

    def test_nothing_listening_does_not_exit(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        os.environ["MT_HOST"] = "127.0.0.1"
        os.environ["MT_PORT"] = str(port)
        kind = desktop_window.wait_until_up("", seconds=0.6)
        self.assertEqual(kind, "down")

    def test_source_does_not_exit_with_the_old_failure(self):
        text = (ROOT / "desktop_window.py").read_text(encoding="utf-8")
        self.assertNotIn("本机服务没有起来", text)


class LaunchOrderTests(unittest.TestCase):
    def test_mac_cold_start_does_not_download_before_the_window(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        cold = text.split("acquire_launch_lock", 1)[1]
        self.assertNotIn("refresh_app", cold)
        self.assertNotIn("codeload.github.com", cold)
        fast = cold.split("# FAST_PATH_START", 1)[1].split("# FAST_PATH_END", 1)[0]
        self.assertLess(fast.index("start_server"), fast.index("open_desktop_window"))
        slow = cold.split("# SLOW_PATH_START", 1)[1].split("# SLOW_PATH_END", 1)[0]
        self.assertLess(slow.index("open_desktop_window"), slow.index("ensure_uv"))
        self.assertLess(cold.index("use_local_app"), cold.index("# FAST_PATH_START"))
        self.assertIn("! helper_ready && ! server_pid_alive", text)

    def test_version_compare(self):
        def newer(a, b):
            proc = subprocess.run(
                ["bash", str(LAUNCHER), "--compare-versions", a, b],
                check=False,
            )
            return proc.returncode == 0

        self.assertTrue(newer("1.3.1", "1.3.0"))
        self.assertFalse(newer("1.3.0", "1.3.1"))
        self.assertFalse(newer("1.3.0", "1.3.0"))
        self.assertTrue(newer("1.10.0", "1.9.0"))

    def test_windows_cold_start_does_not_download_before_the_window(self):
        text = WINDOWS.read_text(encoding="utf-8")
        self.assertNotIn("Refresh-App", text)
        self.assertNotIn("codeload.github.com", text)
        fast = text.split("# FAST_PATH_START", 1)[1].split("# FAST_PATH_END", 1)[0]
        self.assertLess(fast.index("Start-Server"), fast.index("Open-DesktopWindow"))
        slow = text.split("# SLOW_PATH_START", 1)[1].split("# SLOW_PATH_END", 1)[0]
        self.assertLess(slow.index("Open-DesktopWindow"), slow.index("Ensure-Uv"))
        self.assertIn("Test-ServerPidAlive", text)


if __name__ == "__main__":
    unittest.main()
