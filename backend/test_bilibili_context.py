"""Official Bilibili metadata becomes transcription context.

These tests stub HTTP. They do not call Bilibili or Whisper.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import transcription
import video_processor
from audio import Chunk
from engines import Segment, TranscriptionEngine
from engines.base import Capabilities, ChunkResult, EngineInfo, whisper_initial_prompt
from hotwords import extract_hotwords
from text_utils import format_source_context, merge_page_meta
from transcription import run_transcription
from video_processor import VideoProcessor


BV = "BV1m7WB6mEc9"
FULL = f"https://www.bilibili.com/video/{BV}"
PAGE2 = f"{FULL}?p=2"
HUMAN_SUB = "https://i0.hdslb.com/bfs/subtitle/human.json"
AI_SUB = "https://aisubtitle.hdslb.com/bfs/ai_subtitle/auto.json"
LOCKED_SUB = "https://i0.hdslb.com/bfs/subtitle/locked.json"


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
    vp.runtime_cookiefile = "/tmp/mt-bili-context-no-such-cookie"
    vp._guest_buvid_ready = True
    vp._guest_buvid3 = "b3"
    vp._guest_buvid4 = "b4"
    return vp


def _view(**extra) -> dict:
    data = {
        "bvid": BV,
        "title": "和王慧文对谈硬件",
        "cid": 11,
        "duration": 120,
        "owner": {"name": "知行合一的TLW"},
        "desc": "这期请到《王慧文》聊聊做硬件的事",
        "pages": [
            {"page": 1, "cid": 11, "part": "正片", "duration": 80},
            {"page": 2, "cid": 22, "part": "和王慧文对谈", "duration": 55},
        ],
        "staff": [{"name": "知行合一的TLW"}, {"name": "王慧文"}],
        "subtitle": {"list": []},
    }
    data.update(extra)
    return data


def _tags():
    return {"code": 0, "data": [
        {"tag_name": "人工智能"},
        {"tag_name": "分享"},
        {"tag_name": "日常"},
    ]}


def _player(tracks):
    return {"code": 0, "data": {"subtitle": {"subtitles": tracks}}}


def _human_track():
    return {
        "lan": "zh-CN",
        "lan_doc": "中文",
        "is_lock": False,
        "ai_status": 0,
        "subtitle_url": HUMAN_SUB,
    }


def _ai_track():
    return {
        "lan": "ai-zh",
        "lan_doc": "中文（自动生成）",
        "is_lock": False,
        "ai_status": 2,
        "subtitle_url": AI_SUB,
    }


def _caption_body():
    return {"body": [
        {"from": 0, "to": 1, "content": "聊聊硬件"},
        {"from": 1, "to": 2, "content": "聊聊硬件"},
        {"from": 2, "to": 3, "content": "只出现一次"},
    ]}


class _Engine(TranscriptionEngine):
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    @property
    def info(self) -> EngineInfo:
        return EngineInfo("fake", "small", "cpu", "int8", "fake")

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities()

    def load(self, progress=None, cancel=None) -> None:
        return None

    def transcribe_chunk(self, chunk, language=None, word_timestamps=False,
                         condition_on_previous_text=False, initial_prompt=None):
        self.calls.append({"language": language, "prompt": initial_prompt})
        if not self._results:
            raise AssertionError("engine ran out of scripted chunks")
        return self._results.pop(0)


def _seg(text: str, no_speech: float = 0.1) -> Segment:
    return Segment(text=text, start=0.0, end=1.0, no_speech_prob=no_speech)


class BilibiliContextTests(unittest.TestCase):
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

    def _route(self, table):
        def urlopen(req, timeout=None):
            url = req.full_url
            self.urls.append(url)
            for key, body in table.items():
                if key in url:
                    if isinstance(body, Exception):
                        raise body
                    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                    return _Resp(raw)
            raise AssertionError(url)

        video_processor.urllib.request.urlopen = urlopen

    def test_context_assembles_official_fields_and_skips_blanks(self):
        vp = _processor()
        self._route({
            "tag/archive/tags": _tags(),
            "player/v2": _player([_ai_track(), _human_track()]),
            HUMAN_SUB: _caption_body(),
        })
        meta = vp._bilibili_page_meta(_view(), FULL)
        text = format_source_context(**{k: meta[k] for k in (
            "show", "description", "tags", "part", "people", "captions",
        )})
        self.assertEqual(meta["show"], "知行合一的TLW")
        self.assertIn("王慧文", meta["description"])
        self.assertEqual(meta["tags"], ["人工智能", "分享", "日常"])
        self.assertEqual(meta["people"], ["王慧文"])
        self.assertEqual(meta["part"], "")
        self.assertIn("聊聊硬件", meta["captions"])
        self.assertIn("节目：知行合一的TLW", text)
        self.assertIn("嘉宾：王慧文", text)
        self.assertIn("标签：人工智能", text)
        self.assertIn("这期请到", text)
        self.assertIn("字幕：", text)
        self.assertNotIn("分享", text)
        self.assertNotIn("日常", text)
        self.assertNotIn("分集：", text)
        self.assertNotIn("正片", text)
        self.assertNotIn("标签：\n", text)
        self.assertNotIn("嘉宾：\n", text)
        self.assertTrue(any(HUMAN_SUB in u for u in self.urls))
        self.assertFalse(any(AI_SUB in u for u in self.urls))
        self.assertEqual(vp._recall_bili(BV).get("cid"), 11)

    def test_missing_fields_are_omitted(self):
        vp = _processor()
        self._route({
            "tag/archive/tags": {"code": 0, "data": []},
            "player/v2": _player([]),
            "web-interface/nav": {"code": -101, "message": "未登录"},
        })
        meta = vp._bilibili_page_meta({
            "bvid": BV,
            "title": "只有标题",
            "cid": 11,
            "owner": {"name": "某UP"},
        }, FULL)
        text = format_source_context(
            show=meta["show"], description=meta["description"], tags=meta["tags"],
            part=meta["part"], people=meta["people"], captions=meta["captions"],
        )
        self.assertEqual(text, "节目：某UP")
        self.assertNotIn("标签", text)
        self.assertNotIn("分集", text)
        self.assertNotIn("字幕", text)
        self.assertNotIn("嘉宾", text)
        self.assertEqual(format_source_context(
            show="", description="", tags=[], part="", people=[], captions="",
        ), "")
        self.assertFalse(any("player/wbi" in u for u in self.urls))

    def test_desc_v2_and_dynamic_fill_a_blank_intro_once(self):
        vp = _processor()
        self._route({
            "tag/archive/tags": {"code": 0, "data": []},
            "player/v2": _player([]),
            "web-interface/nav": {"code": 0, "data": {}},
        })
        meta = vp._bilibili_page_meta(_view(
            desc="",
            desc_v2=[{"raw_text": "简介正文"}],
            dynamic="简介正文",
            staff=[],
            pages=[],
        ), FULL)
        self.assertEqual(meta["description"], "简介正文")
        self.assertEqual(meta["description"].count("简介正文"), 1)
        self.assertNotIn("未知", meta["description"])

    def test_part_title_selects_that_page(self):
        vp = _processor()
        cid, part, duration = vp._bilibili_target(_view(), PAGE2)
        self.assertEqual(cid, 22)
        self.assertEqual(part, "和王慧文对谈")
        self.assertEqual(duration, 55)
        cid, part, duration = vp._bilibili_target(_view(), FULL)
        self.assertEqual(cid, 11)
        self.assertEqual(part, "")
        self.assertEqual(duration, 80)

    def test_locked_and_charge_captions_are_not_fetched(self):
        vp = _processor()
        self._route({
            "tag/archive/tags": {"code": 0, "data": []},
            "player/v2": _player([{
                "lan": "zh-CN",
                "is_lock": True,
                "subtitle_url": LOCKED_SUB,
            }]),
        })
        meta = vp._bilibili_page_meta(_view(), FULL)
        self.assertEqual(meta["captions"], "")
        self.assertFalse(any("locked.json" in u for u in self.urls))

        self.urls.clear()
        meta = vp._bilibili_page_meta(_view(
            is_upower_exclusive=True,
            is_upower_play=False,
            subtitle={"list": [_human_track()]},
        ), FULL)
        self.assertEqual(meta["captions"], "")
        self.assertEqual(meta["show"], "知行合一的TLW")
        self.assertFalse(any("human.json" in u or "player/v2" in u for u in self.urls))

    def test_wbi_player_supplies_captions_when_v2_is_empty(self):
        vp = _processor()
        self._route({
            "tag/archive/tags": {"code": 0, "data": []},
            "player/v2?": _player([]),
            "web-interface/nav": {"code": -101, "data": {"wbi_img": {
                "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
            }}},
            "player/wbi/v2": _player([_human_track()]),
            HUMAN_SUB: _caption_body(),
        })
        meta = vp._bilibili_page_meta(_view(staff=[], desc=""), FULL)
        self.assertIn("聊聊硬件", meta["captions"])
        self.assertTrue(any("player/wbi/v2" in u and "w_rid=" in u for u in self.urls))

    def test_412_keeps_audio_and_api_context_without_webpage(self):
        vp = _processor()
        audio = "https://example.bilivideo.com/43578819134.m4s"
        self._route({
            "finger/spi": {"code": 0, "data": {"b_3": "b3", "b_4": "b4"}},
            "web-interface/view": {"code": 0, "data": _view()},
            "tag/archive/tags": _tags(),
            "player/v2": _player([_human_track()]),
            HUMAN_SUB: _caption_body(),
            "playurl": {"code": 0, "data": {"dash": {"audio": [
                {"bandwidth": 100, "baseUrl": audio},
            ]}}},
        })

        class YDL:
            downloads = 0

            def __init__(self, *_a, **_k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def extract_info(self, *_a, **_k):
                raise Exception(
                    "ERROR: [BiliBili] Unable to download webpage: "
                    "HTTP Error 412: Precondition Failed"
                )

            def download(self, *_a, **_k):
                YDL.downloads += 1
                raise AssertionError("yt-dlp must not download after the API resolved audio")

        saved = {}

        def fake_dl(audio_url, dest, *_a, **_k):
            saved["url"] = audio_url
            Path(dest).write_bytes(b"m4a")
            return str(dest)

        original_ydl = video_processor.yt_dlp.YoutubeDL
        original_dl = video_processor.audio_utils.download_audio_url_to_m4a
        original_sleep = video_processor.asyncio.sleep
        video_processor.yt_dlp.YoutubeDL = YDL
        video_processor.audio_utils.download_audio_url_to_m4a = fake_dl

        async def fast(*_a, **_k):
            return None

        video_processor.asyncio.sleep = fast
        try:
            parsed = asyncio.run(vp.fetch_subtitles(FULL, Path(tempfile.mkdtemp())))
            meta = parsed[5]
            text = format_source_context(
                show=meta.get("show") or "",
                description=meta.get("description") or "",
                tags=meta.get("tags") or [],
                part=meta.get("part") or "",
                people=meta.get("people") or [],
                captions=meta.get("captions") or "",
            )
            self.assertIsNone(parsed[0])
            self.assertEqual(parsed[1], "和王慧文对谈硬件")
            self.assertFalse(parsed[4])
            self.assertIn("节目：知行合一的TLW", text)
            self.assertIn("人工智能", text)
            self.assertIn("聊聊硬件", text)
            self.assertNotIn("分享", text)
            with tempfile.TemporaryDirectory() as tmp:
                path, title = asyncio.run(
                    vp.download_and_convert(FULL, Path(tmp), prefetched_title=parsed[1])
                )
        finally:
            video_processor.yt_dlp.YoutubeDL = original_ydl
            video_processor.audio_utils.download_audio_url_to_m4a = original_dl
            video_processor.asyncio.sleep = original_sleep
        self.assertEqual(saved["url"], audio)
        self.assertTrue(str(path).endswith(".m4a"))
        self.assertEqual(title, "和王慧文对谈硬件")
        self.assertEqual(YDL.downloads, 0)
        self.assertTrue(any("cid=11" in u and "playurl" in u for u in self.urls))

    def test_second_part_audio_uses_that_cid(self):
        vp = _processor()
        self._route({
            "finger/spi": {"code": 0, "data": {"b_3": "b3", "b_4": "b4"}},
            "web-interface/view": {"code": 0, "data": _view()},
            "tag/archive/tags": {"code": 0, "data": []},
            "player/v2": _player([]),
            "web-interface/nav": {"code": 0, "data": {}},
            "playurl": {"code": 0, "data": {"dash": {"audio": [
                {"bandwidth": 10, "baseUrl": "https://example.bilivideo.com/p2.m4s"},
            ]}}},
        })

        class YDL:
            def __init__(self, *_a, **_k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def extract_info(self, *_a, **_k):
                raise Exception("HTTP Error 412: Precondition Failed")

        original_ydl = video_processor.yt_dlp.YoutubeDL
        original_sleep = video_processor.asyncio.sleep
        video_processor.yt_dlp.YoutubeDL = YDL

        async def fast(*_a, **_k):
            return None

        video_processor.asyncio.sleep = fast
        try:
            parsed = asyncio.run(vp.fetch_subtitles(PAGE2, Path(tempfile.mkdtemp())))
        finally:
            video_processor.yt_dlp.YoutubeDL = original_ydl
            video_processor.asyncio.sleep = original_sleep
        self.assertIn("分集：和王慧文对谈", format_source_context(
            show=parsed[5].get("show") or "",
            part=parsed[5].get("part") or "",
        ))
        self.assertEqual(parsed[3], 55)
        self.assertNotIn("duration", parsed[5])
        self.assertTrue(any("cid=22" in u and "playurl" in u for u in self.urls))
        self.assertFalse(any("cid=11" in u and "playurl" in u for u in self.urls))

    def test_webpage_metadata_only_fills_blanks(self):
        vp = _processor()
        self._route({
            "finger/spi": {"code": 0, "data": {"b_3": "b3", "b_4": "b4"}},
            "web-interface/view": {"code": 0, "data": _view()},
            "tag/archive/tags": _tags(),
            "player/v2": _player([]),
            "web-interface/nav": {"code": 0, "data": {}},
        })

        class YDL:
            def __init__(self, *_a, **_k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def extract_info(self, *_a, **_k):
                return {
                    "title": "网页标题",
                    "duration": 10,
                    "uploader": "网页UP",
                    "description": "网页简介",
                    "tags": ["网页标签"],
                    "subtitles": {},
                    "automatic_captions": {},
                }

        original_ydl = video_processor.yt_dlp.YoutubeDL
        original_sleep = video_processor.asyncio.sleep
        video_processor.yt_dlp.YoutubeDL = YDL

        async def fast(*_a, **_k):
            return None

        video_processor.asyncio.sleep = fast
        try:
            parsed = asyncio.run(vp.fetch_subtitles(FULL, Path(tempfile.mkdtemp())))
        finally:
            video_processor.yt_dlp.YoutubeDL = original_ydl
            video_processor.asyncio.sleep = original_sleep
        meta = parsed[5]
        self.assertEqual(meta["show"], "知行合一的TLW")
        self.assertIn("王慧文", meta["description"])
        self.assertNotIn("网页简介", meta["description"])
        self.assertEqual(meta["tags"], ["人工智能", "分享", "日常"])
        self.assertNotIn("网页标签", meta["tags"])

        filled = merge_page_meta(
            {"show": "官方UP", "description": "", "tags": [], "part": "", "captions": ""},
            {"show": "网页UP", "description": "网页简介", "tags": ["网页标签"]},
        )
        self.assertEqual(filled["show"], "官方UP")
        self.assertEqual(filled["description"], "网页简介")
        self.assertEqual(filled["tags"], ["网页标签"])
        self.assertEqual(filled["part"], "")
        self.assertEqual(filled["captions"], "")

    def test_prompt_uses_context_and_does_not_become_transcript(self):
        meta_text = format_source_context(
            show="知行合一的TLW",
            description="这期请到《王慧文》聊聊做硬件的事",
            tags=["人工智能", "分享"],
            part="和王慧文对谈",
            people=["王慧文"],
            captions="聊聊硬件 聊聊硬件 只出现一次",
        )
        words = extract_hotwords(title="和王慧文对谈硬件", source_context=meta_text)
        self.assertIn("知行合一的TLW", words)
        self.assertIn("王慧文", words)
        self.assertIn("人工智能", words)
        self.assertIn("聊聊硬件", words)
        self.assertNotIn("分享", words)
        self.assertNotIn("只出现一次", words)
        self.assertLess(words.index("王慧文"), words.index("人工智能"))
        prompt = whisper_initial_prompt("zh", source_context=meta_text, title="和王慧文对谈硬件")
        self.assertIn("专名：", prompt)
        self.assertIn("王慧文", prompt)
        self.assertIn("人工智能", prompt)
        self.assertNotIn("字幕：", prompt)
        self.assertNotIn("只出现一次", prompt)
        self.assertNotIn("这期请到", prompt)

        engine = _Engine([
            ChunkResult(
                segments=[
                    _seg("专名：知行合一的TLW、王慧文、人工智能" * 2, 0.2),
                    _seg("今天请到了王慧文"),
                ],
                language="zh",
                language_probability=0.99,
            ),
        ])
        transcription.audio_utils.probe_duration = lambda *_a, **_k: 30.0
        transcription.audio_utils.plan_chunks = lambda *_a, **_k: [Chunk(0, 0.0, 30.0)]
        transcription.settings_store.whisper_speed = lambda: 3.0
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "clip.m4a"
            audio.write_bytes(b"not-audio")
            result = asyncio.run(run_transcription(
                audio,
                engine=engine,
                language=None,
                work_dir=Path(tmp) / "work",
                title="和王慧文对谈硬件",
                source_context=meta_text,
            ))
        text = "".join(s.text for s in result.segments)
        self.assertIn("今天请到了王慧文", text)
        self.assertNotIn("专名", text)
        self.assertNotIn("节目：", text)
        self.assertNotIn("字幕：", text)
        self.assertNotIn("这期请到", text)
        self.assertNotIn("只出现一次", text)
        self.assertTrue((result.language or "").lower().startswith("zh"))


if __name__ == "__main__":
    unittest.main()
