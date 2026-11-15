"""Subtitle language choice and the Chinese transcription default.

These tests stub HTTP. They do not call Bilibili or any other network.
"""
from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path

import video_processor
from engines.base import ZH_PROMPT, prompt_for_language, whisper_initial_prompt
from text_utils import is_chinese_lang, is_english_lang, whisper_language
from video_processor import VideoProcessor

import jobs


BV = "BV1xx411c7mD"
FULL = f"https://www.bilibili.com/video/{BV}"
YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n你好世界\n"


def _processor() -> VideoProcessor:
    vp = VideoProcessor()
    vp.runtime_cookiefile = "/tmp/mt-subtitle-lang-no-such-cookie"
    vp._bilibili_view = lambda _bvid: {"title": "口语", "duration": 12, "owner": {"name": "播主"}}
    return vp


class _ForbidNet(unittest.TestCase):
    def setUp(self):
        self._urlopen = video_processor.urllib.request.urlopen
        video_processor.urllib.request.urlopen = self._forbid

    def tearDown(self):
        video_processor.urllib.request.urlopen = self._urlopen

    def _forbid(self, *args, **kwargs):
        raise AssertionError("this test must not call the network")


class SubtitleRankTests(_ForbidNet):
    def test_chinese_codes_and_english_codes(self):
        for lang in ("zh", "ZH", "zh-Hans", "zh-CN", "zh-TW", "zh-Hant", "zh_CN", "ai-zh", "AI-ZH", "chs", "cht", "ai-zh-Hans"):
            self.assertTrue(is_chinese_lang(lang), lang)
            self.assertFalse(is_english_lang(lang), lang)
        for lang in ("en", "en-US", "en-orig", "ai-en", "AI-EN", "en_GB"):
            self.assertTrue(is_english_lang(lang), lang)
            self.assertFalse(is_chinese_lang(lang), lang)
        self.assertFalse(is_chinese_lang(""))
        self.assertFalse(is_chinese_lang(None))
        self.assertFalse(is_english_lang(None))

    def test_bilibili_rejects_english_and_first_key_fallback(self):
        vp = _processor()
        self.assertEqual(vp._rank_subtitle_langs(FULL, {"en": "auto", "ai-en": "auto"}), [])
        self.assertEqual(list({"ai-en": "auto", "en": "manual"}), ["ai-en", "en"])
        self.assertEqual(vp._rank_subtitle_langs(FULL, {"ai-en": "auto", "en": "manual"}), [])
        self.assertEqual(vp._rank_subtitle_langs(FULL, {"en": "manual", "ja": "manual"}), [])

    def test_bilibili_accepts_ai_zh_and_chs(self):
        vp = _processor()
        self.assertEqual(
            vp._rank_subtitle_langs(FULL, {"en": "manual", "ai-en": "auto", "ai-zh": "auto"}),
            ["ai-zh"],
        )
        self.assertEqual(vp._rank_subtitle_langs(FULL, {"ai-en": "auto", "chs": "manual"}), ["chs"])
        self.assertEqual(vp._rank_subtitle_langs(FULL, {"AI-ZH": "auto"}), ["AI-ZH"])
        self.assertEqual(vp._rank_subtitle_langs(FULL, {"cht": "manual", "en": "manual"}), ["cht"])
        self.assertEqual(
            vp._rank_subtitle_langs(FULL, {"ai-zh": "auto", "zh-Hans": "manual", "en": "manual"}),
            ["zh-Hans", "ai-zh"],
        )
        self.assertEqual(vp._rank_subtitle_langs(FULL, {"zh_TW": "manual"}), ["zh_TW"])
        self.assertTrue(vp._keep_subtitle(FULL, "ai-zh", "ai-zh"))
        self.assertTrue(vp._keep_subtitle(FULL, "chs", "chs"))
        self.assertTrue(vp._keep_subtitle(FULL, "zh-Hant", "zh-Hant"))
        self.assertFalse(vp._keep_subtitle(FULL, "en", "en"))
        self.assertFalse(vp._keep_subtitle(FULL, "ai-en", "ai-zh"))
        self.assertFalse(vp._keep_subtitle(FULL, "", ""))

    def test_other_chinese_platforms_do_not_use_english_subs(self):
        vp = _processor()
        urls = [
            "https://www.xiaohongshu.com/explore/abc",
            "https://v.douyin.com/abc",
            "https://channels.weixin.qq.com/foo",
            "https://www.xiaoyuzhoufm.com/episode/abc",
            "https://podcasts.apple.com/cn/podcast/x/id1?i=2",
        ]
        for url in urls:
            self.assertEqual(vp._rank_subtitle_langs(url, {"en": "manual", "ai-en": "auto"}), [], url)
            self.assertEqual(vp._rank_subtitle_langs(url, {"en": "manual", "chs": "manual"}), ["chs"], url)

    def test_youtube_preference_unchanged(self):
        vp = _processor()
        ranked = vp._rank_subtitle_langs(YT, {"zh-Hans": "manual", "en": "manual", "ja": "manual"})
        self.assertEqual(ranked, ["en", "zh-Hans", "ja"])
        self.assertEqual(vp._rank_subtitle_langs(YT, {"fr": "manual"}), ["fr"])
        self.assertEqual(vp._rank_subtitle_langs(YT, {"ai-en": "auto"}), [])
        self.assertTrue(vp._keep_subtitle(YT, "en", "en"))

    def test_non_youtube_other_site_skips_english_only(self):
        vp = _processor()
        url = "https://example.com/watch/1"
        self.assertEqual(vp._rank_subtitle_langs(url, {"en-US": "manual"}), [])
        self.assertEqual(vp._rank_subtitle_langs(url, {"en": "manual", "fr": "manual"}), ["fr"])
        self.assertEqual(vp._rank_subtitle_langs(url, {"de-DE": "manual"}), ["de-DE"])


class SubtitleFetchTests(_ForbidNet):
    def _fetch(self, info, write_lang):
        vp = _processor()
        calls = {"langs": []}

        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=False):
                return info

            def download(self, urls):
                langs = list(self.opts.get("subtitleslangs") or [])
                calls["langs"].append(langs)
                if not write_lang or langs != [write_lang]:
                    return
                dest = Path(self.opts["outtmpl"]).parent / f"sub.{write_lang}.vtt"
                dest.write_text(VTT, encoding="utf-8")

        original = video_processor.yt_dlp.YoutubeDL
        video_processor.yt_dlp.YoutubeDL = FakeYDL
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = asyncio.run(vp.fetch_subtitles(FULL, Path(tmp)))
        finally:
            video_processor.yt_dlp.YoutubeDL = original
        return result, calls

    def test_english_only_bilibili_subs_fall_back_to_audio(self):
        info = {
            "title": "口语",
            "duration": 12,
            "subtitles": {"en": [{"ext": "vtt"}]},
            "automatic_captions": {"ai-en": [{"ext": "vtt"}]},
        }
        (segs, _title, lang, _dur, _charge, _meta), calls = self._fetch(info, None)
        self.assertIsNone(segs)
        self.assertIsNone(lang)
        self.assertEqual(calls["langs"], [])

    def test_ai_zh_and_chs_are_used(self):
        for code, manual in (("ai-zh", False), ("chs", True)):
            subs = {}
            auto = {}
            bucket = subs if manual else auto
            bucket[code] = [{"ext": "vtt"}]
            bucket["en"] = [{"ext": "vtt"}]
            info = {
                "title": "口语",
                "duration": 12,
                "subtitles": subs,
                "automatic_captions": auto,
            }
            (segs, _title, lang, _dur, _charge, _meta), calls = self._fetch(info, code)
            self.assertEqual(calls["langs"], [[code]], code)
            self.assertEqual(lang, code)
            self.assertIsNotNone(segs)
            self.assertIn("你好", segs[0].text)


class WhisperLanguageTests(unittest.TestCase):
    def test_chinese_platforms_default_to_zh_and_prompt(self):
        cases = [
            FULL,
            "https://b23.tv/abc",
            "https://www.xiaoyuzhoufm.com/episode/abc",
            "https://www.xiaohongshu.com/explore/abc",
            "https://xhslink.com/abc",
            "https://v.douyin.com/abc",
            "https://channels.weixin.qq.com/foo",
            "https://podcasts.apple.com/cn/podcast/x/id1?i=2",
        ]
        for url in cases:
            self.assertEqual(whisper_language(url, "url", None), "zh", url)
            self.assertEqual(whisper_language(url, "url", "en"), "zh", url)
        self.assertIsNone(whisper_language(YT, "url", None))
        self.assertIsNone(whisper_language(YT, "url", "en"))
        self.assertEqual(whisper_language(YT, "url", "zh-Hant"), "zh")
        self.assertEqual(whisper_language(YT, "url", "ai-zh"), "zh")
        self.assertIsNone(whisper_language("/tmp/talk.m4a", "upload", None))

        self.assertEqual(prompt_for_language("zh"), ZH_PROMPT)
        self.assertEqual(prompt_for_language("ai-zh"), ZH_PROMPT)
        self.assertEqual(prompt_for_language("chs"), ZH_PROMPT)
        self.assertEqual(prompt_for_language("cht"), ZH_PROMPT)
        self.assertEqual(prompt_for_language("zh-TW"), ZH_PROMPT)
        self.assertIsNone(prompt_for_language("en"))
        self.assertIsNone(prompt_for_language(None))
        for lang in ("zh", "ai-zh", "chs", "zh-Hant"):
            self.assertIn(ZH_PROMPT, whisper_initial_prompt(lang) or "")
        self.assertNotIn(ZH_PROMPT, whisper_initial_prompt("en") or "")

    def test_job_execute_uses_zh_default_and_drops_english_subs(self):
        src = inspect.getsource(jobs.JobManager._execute)
        self.assertIn("whisper_language(", src)
        self.assertIn("_keep_subtitle(", src)


if __name__ == "__main__":
    unittest.main()
