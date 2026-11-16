"""Bilibili multi-part URLs stay on the requested part.

P2 is a different task from P1 of the same BV, and a cached P1 audio URL
must not be downloaded for p=2. These tests stub HTTP. They do not call
Bilibili or Whisper.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import video_processor
from jobs import JobManager
from task_store import COMPLETED, QUEUED, TaskStore
from text_utils import canonical_source
from video_processor import VideoProcessor


BV = "BV1m7WB6mEc9"
P1 = (
    "https://www.bilibili.com/video/BV1m7WB6mEc9/"
    "?spm_id_from=333.788.videopod.episodes"
    "&vd_source=78e38bc82442cd94be26271ca5163032"
)
P2 = P1 + "&p=2"
P1_EXPLICIT = P1 + "&p=1"
CANON = f"https://www.bilibili.com/video/{BV}"
P1_AUDIO = "https://example.bilivideo.com/p1.m4s"
P2_AUDIO = "https://example.bilivideo.com/p2.m4s"


def _view() -> dict:
    return {
        "bvid": BV,
        "title": "TLW的直播间",
        "cid": 11,
        "duration": 7217,
        "owner": {"name": "TLW"},
        "desc": "",
        "pages": [
            {"page": 1, "cid": 11, "part": "0-TLW的直播间", "duration": 7217},
            {"page": 2, "cid": 22, "part": "1-TLW的直播间", "duration": 3371},
        ],
    }


def _processor() -> VideoProcessor:
    vp = VideoProcessor()
    vp.runtime_cookiefile = "/tmp/mt-bili-parts-no-such-cookie"
    vp._guest_buvid_ready = True
    vp._guest_buvid3 = "b3"
    vp._guest_buvid4 = "b4"
    return vp


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class BilibiliPartIdentityTests(unittest.TestCase):
    def test_part_query_survives_tracking_params(self):
        self.assertEqual(canonical_source(P1), f"bilibili:{BV}")
        self.assertEqual(canonical_source(P1_EXPLICIT), canonical_source(P1))
        self.assertEqual(canonical_source(P2), f"bilibili:{BV}:p2")
        self.assertEqual(
            canonical_source(f"{CANON}?spm_id_from=333.788.videopod.episodes&p=2&vd_source=abc"),
            f"bilibili:{BV}:p2",
        )
        self.assertEqual(
            canonical_source(f"{CANON}?p=02"),
            f"bilibili:{BV}:p2",
        )
        self.assertEqual(
            canonical_source(f"https://m.bilibili.com/video/{BV}?p=2"),
            f"bilibili:{BV}:p2",
        )
        self.assertEqual(
            canonical_source("https://www.bilibili.com/video/av170001?p=3&spm_id_from=333"),
            "bilibili:av170001:p3",
        )
        self.assertEqual(
            canonical_source("https://www.bilibili.com/video/av170001"),
            "bilibili:av170001",
        )
        self.assertEqual(
            canonical_source("https://b23.tv/abc123?p=2"),
            "b23:abc123:p2",
        )

    def test_prepare_url_keeps_the_part_and_drops_tracking(self):
        vp = _processor()
        self.assertEqual(vp.prepare_url(P1), CANON)
        self.assertEqual(vp.prepare_url(P1_EXPLICIT), CANON)
        self.assertEqual(vp.prepare_url(P2), f"{CANON}?p=2")
        self.assertEqual(
            vp.prepare_url(f"{CANON}/?p=2&spm_id_from=333.788.videopod.episodes"),
            f"{CANON}?p=2",
        )

    def test_p2_does_not_reuse_p1_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = TaskStore(root / "tasks.json", root / "transcripts")
            done = store.create(
                source=P1,
                source_type="url",
                title="0-TLW的直播间",
                status=COMPLETED,
                session_id="sess",
                files={"txt": "t.txt"},
            )
            folder = store.task_dir(done.id)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "t.txt").write_text("第一集", encoding="utf-8")
            jobs = JobManager(store)

            async def _quiet(*_a, **_k):
                return None

            jobs.broadcast = _quiet

            self.assertEqual(jobs.find_completed(P1, "sess").id, done.id)
            self.assertEqual(jobs.find_completed(P1_EXPLICIT, "sess").id, done.id)
            self.assertIsNone(jobs.find_completed(P2, "sess"))

            active = store.create(
                source=P1,
                source_type="url",
                title="0-TLW的直播间",
                status=QUEUED,
                session_id="sess",
            )
            self.assertEqual(jobs.find_active(P1, "sess").id, active.id)
            self.assertIsNone(jobs.find_active(P2, "sess"))

            async def submit():
                fresh = await jobs.submit_url(P2, "sess")
                again = await jobs.submit_url(P1, "sess")
                same_part = await jobs.submit_url(P1_EXPLICIT, "sess")
                return again, fresh, same_part

            again, fresh, same_part = asyncio.run(submit())

        self.assertNotEqual(fresh.id, done.id)
        self.assertNotEqual(fresh.id, active.id)
        self.assertEqual(fresh.status, QUEUED)
        self.assertEqual(fresh.source, P2)
        self.assertEqual(again.id, active.id)
        self.assertEqual(same_part.id, active.id)
        self.assertNotEqual(canonical_source(fresh.source), canonical_source(done.source))


class BilibiliPartAudioTests(unittest.TestCase):
    def setUp(self):
        self._urlopen = video_processor.urllib.request.urlopen
        self._sleep = video_processor.time.sleep
        video_processor.time.sleep = lambda *_a, **_k: None
        video_processor.urllib.request.urlopen = self._forbid
        self.urls = []

    def tearDown(self):
        video_processor.urllib.request.urlopen = self._urlopen
        video_processor.time.sleep = self._sleep

    def _forbid(self, *args, **kwargs):
        raise AssertionError("this test must not call the network")

    def _route_playurl(self):
        def urlopen(req, timeout=None):
            url = req.full_url
            self.urls.append(url)
            if "playurl" not in url:
                raise AssertionError(url)
            audio = P2_AUDIO if "cid=22" in url else P1_AUDIO
            body = json.dumps({"code": 0, "data": {"dash": {"audio": [
                {"bandwidth": 10, "baseUrl": audio},
            ]}}}).encode()
            return _Resp(body)

        video_processor.urllib.request.urlopen = urlopen

    def _download(self, vp: VideoProcessor, url: str) -> str:
        saved = {}

        def fake_dl(audio_url, dest, *_a, **_k):
            saved["url"] = audio_url
            Path(dest).write_bytes(b"m4a")
            return str(dest)

        original = video_processor.audio_utils.download_audio_url_to_m4a
        video_processor.audio_utils.download_audio_url_to_m4a = fake_dl
        try:
            with tempfile.TemporaryDirectory() as tmp:
                asyncio.run(vp._download_bilibili_api(url, Path(tmp), "part"))
        finally:
            video_processor.audio_utils.download_audio_url_to_m4a = original
        return saved["url"]

    def test_cached_first_part_audio_is_not_used_for_page_two(self):
        vp = _processor()
        vp._remember_bili(BV, view=_view(), cid=11, audio_url=P1_AUDIO)
        self._route_playurl()
        got = self._download(vp, P2)
        self.assertEqual(got, P2_AUDIO)
        self.assertTrue(any("cid=22" in u and "playurl" in u for u in self.urls))
        self.assertFalse(any("cid=11" in u and "playurl" in u for u in self.urls))
        self.assertEqual(vp._recall_bili(BV).get("audio_url"), P2_AUDIO)

    def test_switching_part_drops_the_previous_audio_url(self):
        vp = _processor()
        vp._remember_bili(BV, view=_view(), cid=11, audio_url=P1_AUDIO)
        vp._remember_bili(BV, cid=22)
        hit = vp._recall_bili(BV)
        self.assertEqual(hit.get("cid"), 22)
        self.assertFalse(hit.get("audio_url"))

    def test_first_part_still_reuses_cached_audio(self):
        vp = _processor()
        vp._remember_bili(BV, view=_view(), cid=11, audio_url=P1_AUDIO)
        self._route_playurl()
        got = self._download(vp, P1)
        self.assertEqual(got, P1_AUDIO)
        self.assertFalse(any("playurl" in u for u in self.urls))
        got_explicit = self._download(vp, P1_EXPLICIT)
        self.assertEqual(got_explicit, P1_AUDIO)
        self.assertFalse(any("playurl" in u for u in self.urls))


if __name__ == "__main__":
    unittest.main()
