"""Tests for the Netscape cookies.txt parser used by import_bili_cookies."""
from __future__ import annotations

from video_split.import_bili_cookies import _parse_netscape_cookies


def _write(tmp_path, lines):
    p = tmp_path / "cookies.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


def test_parse_extracts_all_fields(tmp_path):
    path = _write(tmp_path, [
        "# Netscape HTTP Cookie File",
        ".bilibili.com\tTRUE\t/\tTRUE\t1784688576\tSESSDATA\ta12a3698%2C1800162961%2C3abcd",
        ".bilibili.com\tTRUE\t/\tTRUE\t1784688576\tbili_jct\tb79e55161481",
        ".bilibili.com\tTRUE\t/\tFALSE\t1784688576\tbuvid3\tAF0140B0-infoc",
        ".bilibili.com\tTRUE\t/\tFALSE\t1784688576\tbuvid4\t21B85D85-xyz",
    ])
    cookies = _parse_netscape_cookies(path)
    assert cookies["SESSDATA"].startswith("a12a3698")
    assert cookies["bili_jct"] == "b79e55161481"
    assert cookies["buvid3"] == "AF0140B0-infoc"
    assert cookies["buvid4"] == "21B85D85-xyz"


def test_parse_skips_comments_and_blank(tmp_path):
    path = _write(tmp_path, [
        "# comment",
        "",
        "# https://curl.haxx.se/rfc/cookie_spec.html",
        ".bilibili.com\tTRUE\t/\tTRUE\t0\tb_nut\t1781519068",
    ])
    cookies = _parse_netscape_cookies(path)
    assert cookies == {"b_nut": "1781519068"}


def test_parse_ignores_malformed_short_lines(tmp_path):
    path = _write(tmp_path, [
        ".bilibili.com\tTRUE\t/",  # too few fields
        ".bilibili.com\tTRUE\t/\tTRUE\t0\tbili_ticket\tey.jwt.sig",
    ])
    cookies = _parse_netscape_cookies(path)
    assert cookies == {"bili_ticket": "ey.jwt.sig"}
