"""Apple Podcasts resolution without yt-dlp's JS asset regex.

These tests stub HTTP. A public episode that failed in the 2026-11-18 journal
(same shape: podcasts.apple.com/de/podcast/…?i=…) is:

https://podcasts.apple.com/de/podcast/are-american-tech-billionaires-beyond-all-control/id1611374685?i=1000802453455

yt-dlp's API fallback only accepts /assets/index~[0-9a-f]+.js. The live page
serves /assets/index~Bfya77aKeo.js, so a page with no stream raises
"Unable to extract JS asset path". Looking up that episode id alone on
iTunes returns zero results; the show id in /id1611374685 does not.
"""
from __future__ import annotations

import asyncio
import re
import unittest
from unittest.mock import patch

import podcast_extractor
from text_utils import (
    APPLE_EPISODE_HINT,
    APPLE_EXTRACT_HINT,
    INVALID_LINK_HINT,
    apple_episode_id,
    apple_show_id,
    friendly_error,
)

EPISODE_URL = (
    "https://podcasts.apple.com/de/podcast/"
    "are-american-tech-billionaires-beyond-all-control/id1611374685?i=1000802453455"
)
SHOW_URL = "https://podcasts.apple.com/de/podcast/the-rest-is-politics/id1611374685"
AUDIO = "https://pdst.fm/e/traffic.megaphone.fm/GLT9978547937.mp3"
# The character class in yt-dlp 2026.11.23 AppleBaseIE._get_token.
_YTDLP_JS_ASSET = re.compile(r'<script [^>]*\bsrc="(/assets/index~[0-9a-f]+\.js)">')
_LIVE_SCRIPT = '<script type="module" crossorigin src="/assets/index~Bfya77aKeo.js"></script>'


class _Resp:
    def __init__(self, payload=None, text=""):
        self._payload = payload if payload is not None else {}
        self.text = text
        self.encoding = None

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _itunes_payload():
    return {
        "resultCount": 2,
        "results": [
            {
                "wrapperType": "track",
                "kind": "podcast",
                "trackId": 1611374685,
                "collectionName": "The Rest Is Politics",
            },
            {
                "wrapperType": "podcastEpisode",
                "kind": "podcast-episode",
                "trackId": 1000802453455,
                "trackName": "Are American Tech Billionaires Beyond All Control?",
                "collectionName": "The Rest Is Politics",
                "episodeUrl": AUDIO,
                "trackTimeMillis": 4070000,
                "description": "<p>Rory and Marina.</p>",
                "artworkUrl600": "https://example.com/art.jpg",
            },
        ],
    }


class ApplePodcastTests(unittest.TestCase):
    def test_js_asset_name_is_not_hex(self):
        self.assertIsNone(_YTDLP_JS_ASSET.search(_LIVE_SCRIPT))
        self.assertIn("index~Bfya77aKeo.js", _LIVE_SCRIPT)

    def test_episode_id_lookup_is_empty_show_id_is_not(self):
        self.assertEqual(apple_episode_id(EPISODE_URL), "1000802453455")
        self.assertEqual(apple_show_id(EPISODE_URL), "1611374685")
        self.assertEqual(apple_episode_id(SHOW_URL), "")

    def test_show_link_asks_for_one_episode(self):
        with patch("requests.get", side_effect=AssertionError("no network")):
            with self.assertRaises(ValueError) as caught:
                asyncio.run(podcast_extractor._fetch_apple_podcasts(SHOW_URL))
        self.assertEqual(str(caught.exception), APPLE_EPISODE_HINT)

    def test_itunes_show_lookup_resolves_when_ytdlp_cannot(self):
        calls = []

        def fake_get(url, params=None, **_kwargs):
            if "itunes.apple.com" in str(url):
                calls.append(str((params or {}).get("id")))
                looked = str((params or {}).get("id"))
                if looked == "1000802453455":
                    return _Resp({"resultCount": 0, "results": []})
                if looked == "1611374685":
                    return _Resp(_itunes_payload())
                return _Resp({"resultCount": 0, "results": []})
            raise RuntimeError("apple webpage unavailable")

        async def ytdlp_broken(*_args, **_kwargs):
            raise Exception(
                "[apple:podcasts] 1000802453455: Unable to extract JS asset path"
            )

        with patch("requests.get", fake_get), patch.object(
            podcast_extractor, "_ytdlp_extract_info", ytdlp_broken
        ):
            info = asyncio.run(podcast_extractor._fetch_apple_podcasts(EPISODE_URL))
        self.assertEqual(info.audio_url, AUDIO)
        self.assertEqual(info.title, "Are American Tech Billionaires Beyond All Control?")
        self.assertEqual(info.podcast_name, "The Rest Is Politics")
        self.assertEqual(info.duration, 4070)
        self.assertIn("Rory and Marina", info.description)
        self.assertIn("1611374685", calls)
        self.assertNotIn("1000802453455", calls)

    def test_page_stream_when_itunes_has_no_episode(self):
        html = (
            '<script id="serialized-server-data">'
            '{"data":[{"data":{"headerButtonItems":[{"$kind":"share",'
            '"model":{"title":"From the page","showTitle":"Show Name","duration":90,'
            '"summary":"Shownotes","playAction":{"episodeOffer":'
            '{"streamUrl":"https://cdn.example/episode.mp3"}}}}]}}]}'
            "</script>"
        )

        def fake_get(url, params=None, **_kwargs):
            if "itunes.apple.com" in str(url):
                return _Resp({"resultCount": 0, "results": []})
            return _Resp(text=html)

        async def ytdlp_unused(*_args, **_kwargs):
            raise AssertionError("page stream should win before yt-dlp")

        with patch("requests.get", fake_get), patch.object(
            podcast_extractor, "_ytdlp_extract_info", ytdlp_unused
        ):
            info = asyncio.run(podcast_extractor._fetch_apple_podcasts(EPISODE_URL))
        self.assertEqual(info.audio_url, "https://cdn.example/episode.mp3")
        self.assertEqual(info.title, "From the page")
        self.assertEqual(info.podcast_name, "Show Name")
        self.assertEqual(info.duration, 90)
        self.assertIn("Shownotes", info.description)

    def test_valid_episode_extract_failure_is_not_an_invalid_link(self):
        def fake_get(url, params=None, **_kwargs):
            if "itunes.apple.com" in str(url):
                return _Resp({"resultCount": 0, "results": []})
            raise RuntimeError("webpage down")

        async def ytdlp_broken(*_args, **_kwargs):
            raise Exception("Unable to extract JS asset path")

        with patch("requests.get", fake_get), patch.object(
            podcast_extractor, "_ytdlp_extract_info", ytdlp_broken
        ):
            with self.assertRaises(ValueError) as caught:
                asyncio.run(podcast_extractor._fetch_apple_podcasts(EPISODE_URL))
        self.assertEqual(str(caught.exception), APPLE_EXTRACT_HINT)
        shown = friendly_error(
            "ERROR: [apple:podcasts] 1000802453455: Unable to extract JS asset path"
        )
        self.assertEqual(shown, APPLE_EXTRACT_HINT)
        self.assertNotEqual(shown, INVALID_LINK_HINT)
        self.assertNotIn("链接无效", shown)
        self.assertEqual(
            friendly_error("Unable to extract og:title"),
            INVALID_LINK_HINT,
        )


if __name__ == "__main__":
    unittest.main()
