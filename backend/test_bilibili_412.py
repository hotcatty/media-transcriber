"""Local checks for Bilibili short-link expansion and the one-retry block path.

These tests stub HTTP. They do not call Bilibili.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import video_processor
from text_utils import (
    BILI_RISK_HINT,
    BILI_SHORT_LINK_HINT,
    CHARGE_NEED_LOGIN_HINT,
    CHARGE_VIDEO_HINT,
    DOWNLOAD_FAIL_HINT,
    LOGIN_PARSE_HINT,
    bili_block_hint,
    fail_hint_for_task,
    friendly_error,
    is_charge_blocked,
    is_charge_gated,
    is_charge_need_login,
    is_login_required,
)
from video_processor import VideoProcessor, _BiliBlocked
import config


BV = "BV1xx411c7mD"
FULL = f"https://www.bilibili.com/video/{BV}"


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _processor() -> VideoProcessor:
    vp = VideoProcessor()
    vp.runtime_cookiefile = "/tmp/mt-bili-412-no-such-cookie"
    return vp


class Bilibili412Tests(unittest.TestCase):
    def setUp(self):
        self._urlopen = video_processor.urllib.request.urlopen
        self._sleep = video_processor.time.sleep
        video_processor.time.sleep = lambda *_a, **_k: None
        video_processor.urllib.request.urlopen = self._forbid

    def tearDown(self):
        video_processor.urllib.request.urlopen = self._urlopen
        video_processor.time.sleep = self._sleep

    def _forbid(self, *args, **kwargs):
        raise AssertionError("this test must not call the network")

    def test_short_link_expands_before_api_shape(self):
        vp = _processor()
        hops = []

        def hop(url):
            hops.append(url)
            return f"{FULL}?spm_id_from=333.788.recommend_more_video.0"

        vp._one_hop_location = hop
        out = vp.prepare_url("前面的字 https://b23.tv/abc123 后面的字")
        self.assertEqual(out, FULL)
        self.assertEqual(hops, ["https://b23.tv/abc123"])
        self.assertTrue(vp._bili_input_short)

    def test_b23_with_bvid_slug_still_expands(self):
        vp = _processor()
        hops = []

        def hop(url):
            hops.append(url)
            return f"{FULL}?spm_id_from=1"

        vp._one_hop_location = hop
        out = vp.prepare_url(f"https://b23.tv/{BV}")
        self.assertEqual(out, FULL)
        self.assertEqual(len(hops), 1)
        self.assertTrue(vp._bili_input_short)

    def test_full_video_url_is_not_a_short_link(self):
        vp = _processor()

        def hop(_url):
            raise AssertionError("full video URL must not be expanded")

        vp._one_hop_location = hop
        out = vp.prepare_url(f"{FULL}?p=2&spm_id_from=333")
        self.assertEqual(out, f"{FULL}?p=2")
        self.assertFalse(vp._bili_input_short)
        av = vp.prepare_url("https://www.bilibili.com/video/av170001")
        self.assertEqual(av, "https://www.bilibili.com/video/av170001")
        self.assertFalse(vp._bili_input_short)

    def test_mobile_share_url_is_short_and_canonical(self):
        vp = _processor()

        def hop(_url):
            raise AssertionError("mobile URL that already has a BV must not hop")

        vp._one_hop_location = hop
        out = vp.prepare_url(f"https://m.bilibili.com/video/{BV}")
        self.assertEqual(out, FULL)
        self.assertTrue(vp._bili_input_short)

    def test_short_link_412_retries_once_then_asks_for_full_url(self):
        vp = _processor()
        hops = {"n": 0}

        def hop(_url):
            hops["n"] += 1
            raise _BiliBlocked()

        vp._one_hop_location = hop
        with self.assertRaises(Exception) as caught:
            vp.prepare_url("https://b23.tv/abc123")
        self.assertEqual(str(caught.exception), BILI_SHORT_LINK_HINT)
        self.assertNotIn("412", str(caught.exception))
        self.assertNotIn("登录", str(caught.exception))
        self.assertEqual(hops["n"], 2)

    def test_visitor_id_is_reused_until_refresh(self):
        vp = _processor()
        calls = {"spi": 0}

        def urlopen(req, timeout=None):
            self.assertIn("finger/spi", req.full_url)
            calls["spi"] += 1
            body = json.dumps({"code": 0, "data": {"b_3": "buvid-3", "b_4": "buvid-4"}}).encode()
            return _Resp(body)

        video_processor.urllib.request.urlopen = urlopen
        first = vp._bilibili_api_cookie()
        second = vp._bilibili_api_cookie()
        self.assertEqual(calls["spi"], 1)
        self.assertEqual(first, second)
        self.assertIn("buvid3=buvid-3", first)
        self.assertIn("buvid4=buvid-4", second)
        opts = vp._ydl_opts(FULL, {})
        headers = opts["http_headers"]
        self.assertIn("Mozilla", headers["User-Agent"])
        self.assertEqual(headers["Referer"], "https://www.bilibili.com/")
        self.assertIn("buvid3=buvid-3", headers["Cookie"])
        self.assertEqual(calls["spi"], 1)

    def test_412_refreshes_visitor_once(self):
        vp = _processor()
        vp._bili_input_short = True
        spi = {"n": 0}
        views = []

        def urlopen(req, timeout=None):
            if "finger/spi" in req.full_url:
                spi["n"] += 1
                n = spi["n"]
                body = json.dumps(
                    {"code": 0, "data": {"b_3": f"b3-{n}", "b_4": f"b4-{n}"}}
                ).encode()
                return _Resp(body)
            views.append(req.get_header("Cookie") or "")
            self.assertIn("bilibili.com", req.get_header("Referer") or "")
            self.assertTrue(req.get_header("User-agent"))
            body = json.dumps({"code": -412, "message": "请求被拦截"}).encode()
            return _Resp(body)

        video_processor.urllib.request.urlopen = urlopen
        with self.assertRaises(Exception) as caught:
            vp._bilibili_view(BV)
        self.assertEqual(str(caught.exception), BILI_SHORT_LINK_HINT)
        self.assertNotIn("412", str(caught.exception))
        self.assertNotIn("登录", str(caught.exception))
        self.assertNotIn("请求被拦截", str(caught.exception))
        self.assertEqual(spi["n"], 2)
        self.assertEqual(len(views), 2)
        self.assertIn("b3-1", views[0])
        self.assertIn("b4-1", views[0])
        self.assertIn("b3-2", views[1])
        self.assertNotIn("b3-1", views[1])

    def test_full_url_412_says_try_later(self):
        vp = _processor()
        vp.prepare_url(FULL)
        hits = {"n": 0}
        api = {"n": 0}

        def urlopen(req, timeout=None):
            hits["n"] += 1
            if "finger/spi" in req.full_url:
                body = json.dumps({"code": 0, "data": {"b_3": "b3", "b_4": "b4"}}).encode()
                return _Resp(body)
            api["n"] += 1
            if api["n"] <= 2:
                body = json.dumps({"code": -412, "message": "请求被拦截"}).encode()
                return _Resp(body)
            raise AssertionError("retried more than once")

        video_processor.urllib.request.urlopen = urlopen
        with self.assertRaises(Exception) as caught:
            vp._bilibili_json(f"https://api.bilibili.com/x/web-interface/view?bvid={BV}")
        self.assertEqual(str(caught.exception), BILI_RISK_HINT)
        self.assertFalse(vp._bili_input_short)
        self.assertNotIn("短链", str(caught.exception))
        self.assertNotIn("412", str(caught.exception))
        self.assertEqual(api["n"], 2)

    def test_http_412_retries_once(self):
        vp = _processor()
        vp._bili_input_short = False
        hits = {"api": 0, "spi": 0}

        def urlopen(req, timeout=None):
            if "finger/spi" in req.full_url:
                hits["spi"] += 1
                body = json.dumps(
                    {"code": 0, "data": {"b_3": f"b3-{hits['spi']}", "b_4": "b4"}}
                ).encode()
                return _Resp(body)
            hits["api"] += 1
            raise HTTPError(req.full_url, 412, "Precondition Failed", Message(), None)

        video_processor.urllib.request.urlopen = urlopen
        with self.assertRaises(Exception) as caught:
            vp._bilibili_json(f"https://api.bilibili.com/x/web-interface/view?bvid={BV}")
        self.assertEqual(str(caught.exception), BILI_RISK_HINT)
        self.assertEqual(hits["api"], 2)
        self.assertEqual(hits["spi"], 2)

    def test_session_cookie_stays_in_memory(self):
        vp = _processor()
        cookie_path = Path(config.COOKIE_FILE)
        existed = cookie_path.exists()
        before = cookie_path.stat().st_mtime if existed else None
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
            tmp.write(
                ".bilibili.com\tTRUE\t/\tTRUE\t1893456000\tSESSDATA\tfake-session-value\n"
            )
            session_path = tmp.name
        original = Path(session_path).read_text(encoding="utf-8")
        vp.runtime_cookiefile = session_path

        def urlopen(req, timeout=None):
            body = json.dumps({"code": 0, "data": {"b_3": "guest-3", "b_4": "guest-4"}}).encode()
            return _Resp(body)

        video_processor.urllib.request.urlopen = urlopen
        header = vp._bilibili_api_cookie()
        self.assertIn("SESSDATA=fake-session-value", header)
        self.assertIn("buvid3=guest-3", header)
        self.assertEqual(Path(session_path).read_text(encoding="utf-8"), original)
        self.assertEqual(cookie_path.exists(), existed)
        if existed:
            self.assertEqual(cookie_path.stat().st_mtime, before)
        Path(session_path).unlink()

    def test_download_does_not_fall_through_to_ytdlp(self):
        vp = _processor()
        charge_calls = {"n": 0}

        def hop(_url):
            return FULL

        async def fake_api(*_a, **_k):
            raise Exception("HTTP Error 412: Precondition Failed")

        def charge(*_a, **_k):
            charge_calls["n"] += 1
            raise AssertionError("412 must not be treated as a charge video")

        class Boom:
            def __init__(self, *_a, **_k):
                raise AssertionError("yt-dlp should not run after a block")

        vp._one_hop_location = hop
        vp._download_bilibili_api = fake_api
        vp._bilibili_is_charge = charge
        original_ydl = video_processor.yt_dlp.YoutubeDL
        video_processor.yt_dlp.YoutubeDL = Boom
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(Exception) as caught:
                    asyncio.run(vp.download_and_convert("https://b23.tv/abc123", Path(tmp)))
        finally:
            video_processor.yt_dlp.YoutubeDL = original_ydl
        self.assertEqual(str(caught.exception), BILI_SHORT_LINK_HINT)
        self.assertEqual(charge_calls["n"], 0)
        self.assertNotIn("登录", str(caught.exception))

    def test_ydl_412_retries_once(self):
        vp = _processor()
        vp._bili_input_short = False
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise Exception("HTTP Error 412: Precondition Failed")

        orig = video_processor.asyncio.sleep

        async def fast(*_a, **_k):
            return None

        video_processor.asyncio.sleep = fast
        try:
            with self.assertRaises(Exception) as caught:
                asyncio.run(vp._ydl_thread(fn))
        finally:
            video_processor.asyncio.sleep = orig
        self.assertEqual(calls["n"], 2)
        self.assertEqual(str(caught.exception), BILI_RISK_HINT)

    def test_user_copy_never_becomes_login_or_charge(self):
        for text in (
            BILI_SHORT_LINK_HINT,
            BILI_RISK_HINT,
            "HTTP Error 412: Precondition Failed",
            "请求被拦截",
        ):
            shown = friendly_error(text)
            self.assertNotIn("412", shown)
            self.assertNotIn("登录", shown)
            self.assertNotEqual(shown, LOGIN_PARSE_HINT)
            self.assertNotEqual(shown, CHARGE_NEED_LOGIN_HINT)
            self.assertNotEqual(shown, CHARGE_VIDEO_HINT)
            self.assertFalse(is_login_required(text))
            self.assertFalse(is_charge_need_login(text))
            self.assertFalse(is_charge_blocked(text))
            self.assertFalse(is_charge_gated(text))
        self.assertEqual(friendly_error(BILI_SHORT_LINK_HINT), BILI_SHORT_LINK_HINT)
        self.assertEqual(friendly_error("HTTP Error 412: Precondition Failed"), BILI_RISK_HINT)
        self.assertEqual(bili_block_hint(BILI_SHORT_LINK_HINT), BILI_SHORT_LINK_HINT)
        download_hint = fail_hint_for_task(
            "https://b23.tv/abc123", "url", BILI_SHORT_LINK_HINT, "failed", "downloading",
        )
        self.assertEqual(download_hint, BILI_SHORT_LINK_HINT)
        self.assertNotEqual(download_hint, DOWNLOAD_FAIL_HINT)
        full_hint = fail_hint_for_task(
            FULL, "url", BILI_RISK_HINT, "failed", "downloading",
        )
        self.assertEqual(full_hint, BILI_RISK_HINT)

    def test_charge_rules_unchanged(self):
        vp = _processor()
        self.assertTrue(vp._bilibili_charge_locked(
            {"is_upower_exclusive": True, "is_upower_play": False}
        ))
        self.assertTrue(vp._bilibili_charge_locked(
            {"is_upower_preview": 1, "is_upower_play": 0}
        ))
        self.assertFalse(vp._bilibili_charge_locked(
            {"is_upower_exclusive": True, "is_upower_play": True}
        ))
        self.assertEqual(CHARGE_VIDEO_HINT, "当前账号无法观看本充电专属")
        self.assertEqual(CHARGE_NEED_LOGIN_HINT, "充电视频，请读取已充电账号")
        app = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("已读取 token 并重新解析", app)
        self.assertIn("当前账号无法观看本充电专属", app)


if __name__ == "__main__":
    unittest.main()
