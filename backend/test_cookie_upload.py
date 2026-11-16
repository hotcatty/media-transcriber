"""Cookie paste accepted by /api/upload-cookies.

The page tells people to copy the Value cell. These tests stub that paste.
They do not call Bilibili or Douyin.
"""
from __future__ import annotations

import unittest

import cookie_import
from cookie_import import CookieImportError


# Shapes copied from Chrome's Application → Cookies → Value cell.
BILI_VALUE = "2f793ba0,1731600000,8a9b7c*21CjBvExampleToken+/cd=="
DOUYIN_VALUE = "7c8e91ab34cd56ef+7890/ab12cd34=="


def _read(text: str, site: str) -> dict:
    netscape = cookie_import.build_netscape_text(text, site)
    return cookie_import.login_report(netscape, site)


class CookieUploadTests(unittest.TestCase):
    def test_bilibili_value_cell_with_base64_is_sessdata(self):
        report = _read(BILI_VALUE, "bilibili")
        self.assertTrue(report["found"])
        self.assertEqual(report["name"], "SESSDATA")
        self.assertEqual(report["length"], len(BILI_VALUE))
        self.assertIn("读到了 SESSDATA", report["message"])

    def test_douyin_value_cell_with_base64_is_sessionid(self):
        report = _read(DOUYIN_VALUE, "douyin")
        self.assertTrue(report["found"])
        self.assertEqual(report["name"], "sessionid")
        self.assertEqual(report["length"], len(DOUYIN_VALUE))
        self.assertNotIn("SESSDATA", report["message"])

    def test_devtools_name_value_block_and_colon_paste(self):
        block = "\n".join([
            "Name",
            "SESSDATA",
            "Value",
            BILI_VALUE,
        ])
        report = _read(block, "bilibili")
        self.assertTrue(report["found"])
        self.assertEqual(report["length"], len(BILI_VALUE))
        colon = _read("SESSDATA: " + BILI_VALUE, "bilibili")
        self.assertEqual(colon["length"], len(BILI_VALUE))
        spaced = _read("sessionid " + DOUYIN_VALUE, "douyin")
        self.assertEqual(spaced["name"], "sessionid")
        self.assertEqual(spaced["length"], len(DOUYIN_VALUE))

    def test_wrapped_value_is_one_cookie(self):
        cut = 20
        wrapped = BILI_VALUE[:cut] + "\n" + BILI_VALUE[cut:]
        report = _read(wrapped, "bilibili")
        self.assertEqual(report["length"], len(BILI_VALUE))

    def test_header_curl_and_plain_hex_still_read(self):
        header = (
            "ttwid=1|abcdef0123456789; "
            "sessionid=7c8e91ab34cd56ef7890ab12cd34ef56"
        )
        report = _read(header, "douyin")
        self.assertEqual(report["name"], "sessionid")
        self.assertEqual(report["length"], 32)
        curl = (
            "curl 'https://api.bilibili.com' "
            "-H 'Cookie: SESSDATA=2f793ba0%2C173%2A21; bili_jct=abc123def456'"
        )
        bili = _read(curl, "bilibili")
        self.assertTrue(bili["found"])
        self.assertEqual(bili["name"], "SESSDATA")
        plain = _read("2f793ba0c1d24e5f8a9b7c6d5e4f3012", "bilibili")
        self.assertEqual(plain["length"], 32)

    def test_zero_width_in_a_value_is_ignored(self):
        dirty = "2f793ba0c1d2\u200b4e5f8a9b7c6d"
        report = _read(dirty, "bilibili")
        self.assertEqual(report["length"], len(dirty) - 1)

    def test_not_a_cookie_is_still_rejected(self):
        for site, primary, text in (
            ("bilibili", "SESSDATA", "2f793ba…"),
            ("bilibili", "SESSDATA", "请把登录状态贴进来"),
            ("douyin", "sessionid", "不是 sessionid"),
            ("douyin", "sessionid", "short"),
        ):
            with self.assertRaises(CookieImportError) as caught:
                cookie_import.build_netscape_text(text, site)
            self.assertEqual(str(caught.exception), f"没读到 {primary}")


if __name__ == "__main__":
    unittest.main()
