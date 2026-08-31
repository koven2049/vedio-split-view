from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from video_split.config import set_config_path
from video_split.service import bilibili_auth as ba
from video_split.service.bilibili_auth import (
    Fingerprint,
    _bilibili_httpx_proxy,
    _build_exclimb_payload,
    _ticket_hexsign,
    activate_buvid,
    ensure_fingerprint,
    fetch_fingerprint,
    get_bili_ticket,
    load_fingerprint,
    refresh_fingerprint,
    save_fingerprint,
)
from video_split.service.downloader import _bilibili_headers


@pytest.fixture(autouse=True)
def _app_config(test_config_path):
    set_config_path(test_config_path)


class MockResponse:
    def __init__(self, status_code: int, json_data: dict):
        self.status_code = status_code
        self._json = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def _mock_client(response=None, *, raise_exc=None, capture=None):
    """Build a mock httpx.AsyncClient supporting .get/.post."""
    client = MagicMock()

    async def _call(url, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture["kwargs"] = kwargs
        if raise_exc is not None:
            raise raise_exc
        return response

    client.get = MagicMock(side_effect=_call)
    client.post = MagicMock(side_effect=_call)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# --- fingerprint acquisition (finger/spi) ---------------------------------

class TestFetchFingerprint:
    async def test_parses_b3_b4(self):
        resp = MockResponse(200, {"code": 0, "data": {"b_3": "BV3XX", "b_4": "BV4YY"}})
        with patch("httpx.AsyncClient", return_value=_mock_client(resp)):
            b3, b4 = await fetch_fingerprint()
        assert b3 == "BV3XX"
        assert b4 == "BV4YY"

    async def test_non_zero_code_degrades(self):
        resp = MockResponse(200, {"code": -400, "data": {}})
        with patch("httpx.AsyncClient", return_value=_mock_client(resp)):
            b3, b4 = await fetch_fingerprint()
        assert (b3, b4) == ("", "")

    async def test_network_error_degrades(self):
        with patch("httpx.AsyncClient", return_value=_mock_client(raise_exc=RuntimeError("boom"))):
            b3, b4 = await fetch_fingerprint()
        assert (b3, b4) == ("", "")


# --- ExClimbWuzhi activation ----------------------------------------------

class TestExClimbPayload:
    def test_payload_is_wrapped_json_string(self):
        payload = _build_exclimb_payload("BV3XX")
        assert set(payload.keys()) == {"payload"}
        inner = json.loads(payload["payload"])  # must be valid JSON string
        assert inner["3064"] == 1
        # compact separators, no spaces
        assert ", " not in payload["payload"]

    def test_payload_embeds_buvid3(self):
        payload = _build_exclimb_payload("BV_UNIQUE")
        assert "BV_UNIQUE" in payload["payload"]


class TestActivateBuvid:
    async def test_success(self):
        cap: dict = {}
        resp = MockResponse(200, {"code": 0, "data": {}})
        with patch("httpx.AsyncClient", return_value=_mock_client(resp, capture=cap)):
            ok = await activate_buvid("BV3XX", "BV4YY")
        assert ok is True
        assert cap["url"] == ba.BILI_EXCLIMB_URL

    async def test_empty_buvid3_short_circuits(self):
        ok = await activate_buvid("", "")
        assert ok is False

    async def test_non_zero_code_degrades(self):
        resp = MockResponse(200, {"code": -352, "data": {}})
        with patch("httpx.AsyncClient", return_value=_mock_client(resp)):
            ok = await activate_buvid("BV3XX", "BV4YY")
        assert ok is False

    async def test_network_error_does_not_raise(self):
        with patch("httpx.AsyncClient", return_value=_mock_client(raise_exc=RuntimeError("boom"))):
            ok = await activate_buvid("BV3XX", "BV4YY")
        assert ok is False


# --- bili_ticket -----------------------------------------------------------

class TestTicketSign:
    def test_hexsign_matches_hmac_sha256(self):
        ts = 1700000000
        expected = hmac.new(b"XgwSnGZ1p", f"ts{ts}".encode(), hashlib.sha256).hexdigest()
        assert _ticket_hexsign(ts) == expected


class TestGetBiliTicket:
    async def test_success_computes_expiry(self):
        cap: dict = {}
        resp = MockResponse(200, {
            "code": 0,
            "data": {"ticket": "JWT123", "ttl": 259200, "created_at": 1700000000},
        })
        with patch("httpx.AsyncClient", return_value=_mock_client(resp, capture=cap)):
            ticket, expires_at = await get_bili_ticket("csrf123")
        assert ticket == "JWT123"
        assert expires_at == 1700000000 + 259200
        # signed params present
        assert cap["kwargs"]["params"]["key_id"] == "ec02"
        assert cap["kwargs"]["params"]["csrf"] == "csrf123"

    async def test_network_error_degrades(self):
        with patch("httpx.AsyncClient", return_value=_mock_client(raise_exc=RuntimeError("boom"))):
            ticket, expires_at = await get_bili_ticket()
        assert (ticket, expires_at) == ("", 0)


# --- persistence -----------------------------------------------------------

class TestPersistence:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_fingerprint_path", lambda: tmp_path / "fp.json")
        fp = Fingerprint(buvid3="b3", buvid4="b4", b_nut="123", bili_ticket="t",
                         ticket_expires_at=999)
        save_fingerprint(fp)
        loaded = load_fingerprint()
        assert loaded == fp

    def test_load_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_fingerprint_path", lambda: tmp_path / "nope.json")
        assert load_fingerprint() == Fingerprint()


# --- as_cookie_dict expiry gating -----------------------------------------

class TestCookieDict:
    def test_expired_ticket_omitted(self):
        fp = Fingerprint(buvid3="b3", bili_ticket="t", ticket_expires_at=1)  # long past
        d = fp.as_cookie_dict()
        assert "bili_ticket" not in d
        assert d["buvid3"] == "b3"

    def test_valid_ticket_included(self):
        fp = Fingerprint(buvid3="b3", buvid4="b4", b_nut="9",
                         bili_ticket="t", ticket_expires_at=int(time.time()) + 10_000)
        d = fp.as_cookie_dict()
        assert d == {"buvid3": "b3", "buvid4": "b4", "b_nut": "9", "bili_ticket": "t"}


# --- refresh orchestration -------------------------------------------------

class TestRefreshFingerprint:
    async def test_full_refresh_persists_all_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_fingerprint_path", lambda: tmp_path / "fp.json")
        monkeypatch.setattr(ba, "fetch_fingerprint", AsyncMock(return_value=("B3", "B4")))
        monkeypatch.setattr(ba, "activate_buvid", AsyncMock(return_value=True))
        monkeypatch.setattr(ba, "get_bili_ticket",
                            AsyncMock(return_value=("TICKET", int(time.time()) + 259200)))
        fp = await refresh_fingerprint("csrf")
        assert fp.buvid3 == "B3"
        assert fp.buvid4 == "B4"
        assert fp.b_nut  # timestamp seeded
        assert fp.bili_ticket == "TICKET"
        # persisted
        assert load_fingerprint().buvid3 == "B3"

    async def test_spi_failure_keeps_cached_and_still_tries_ticket(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_fingerprint_path", lambda: tmp_path / "fp.json")
        save_fingerprint(Fingerprint(buvid3="OLD", buvid4="OLD4", b_nut="1"))
        monkeypatch.setattr(ba, "fetch_fingerprint", AsyncMock(return_value=("", "")))
        activate = AsyncMock(return_value=True)
        monkeypatch.setattr(ba, "activate_buvid", activate)
        monkeypatch.setattr(ba, "get_bili_ticket",
                            AsyncMock(return_value=("NEWTICKET", int(time.time()) + 100000)))
        fp = await refresh_fingerprint()
        assert fp.buvid3 == "OLD"  # cached retained
        activate.assert_not_called()  # no activation without a fresh buvid3
        assert fp.bili_ticket == "NEWTICKET"

    async def test_valid_cached_ticket_not_refetched(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_fingerprint_path", lambda: tmp_path / "fp.json")
        save_fingerprint(Fingerprint(buvid3="B3", bili_ticket="KEEP",
                                     ticket_expires_at=int(time.time()) + 100000))
        monkeypatch.setattr(ba, "fetch_fingerprint", AsyncMock(return_value=("", "")))
        get_ticket = AsyncMock(return_value=("SHOULD_NOT_USE", 0))
        monkeypatch.setattr(ba, "get_bili_ticket", get_ticket)
        fp = await refresh_fingerprint()
        get_ticket.assert_not_called()
        assert fp.bili_ticket == "KEEP"


class TestEnsureFingerprint:
    async def test_skips_network_when_fresh(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_fingerprint_path", lambda: tmp_path / "fp.json")
        save_fingerprint(Fingerprint(
            buvid3="B3", buvid4="B4", bili_ticket="KEEP",
            ticket_expires_at=int(time.time()) + 100000,
        ))
        refresh = AsyncMock(side_effect=AssertionError("should not refresh"))
        monkeypatch.setattr(ba, "refresh_fingerprint", refresh)
        fp = await ensure_fingerprint()
        refresh.assert_not_called()
        assert fp.buvid3 == "B3"
        assert fp.bili_ticket == "KEEP"

    async def test_refreshes_when_buvid_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_fingerprint_path", lambda: tmp_path / "fp.json")
        refresh = AsyncMock(return_value=Fingerprint(buvid3="NEW"))
        monkeypatch.setattr(ba, "refresh_fingerprint", refresh)
        fp = await ensure_fingerprint("csrf")
        refresh.assert_awaited_once_with("csrf")
        assert fp.buvid3 == "NEW"


class TestBilibiliNeverUsesProxy:
    def test_helper_is_always_none_even_when_proxy_enabled(self, monkeypatch):
        from video_split.config import get_settings
        s = get_settings()
        monkeypatch.setattr(s.network, "proxy_enabled", True)
        monkeypatch.setattr(s.network, "http_proxy", "http://127.0.0.1:7890")
        assert _bilibili_httpx_proxy() is None
        assert s.network.proxy_url == "http://127.0.0.1:7890"

    async def test_spi_constructs_client_without_proxy(self):
        resp = MockResponse(200, {"code": 0, "data": {"b_3": "X", "b_4": "Y"}})
        with patch("httpx.AsyncClient", return_value=_mock_client(resp)) as ctor:
            await fetch_fingerprint()
        assert ctor.call_args.kwargs.get("proxy") is None


# --- Cookie assembly in downloader ----------------------------------------

class TestBilibiliHeadersCookie:
    def test_includes_fingerprint_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_fingerprint_path", lambda: tmp_path / "fp.json")
        save_fingerprint(Fingerprint(buvid3="B3", buvid4="B4", b_nut="777",
                                     bili_ticket="TK", ticket_expires_at=int(time.time()) + 9999))
        headers = _bilibili_headers(sessdata="S", bili_jct="J", buvid3="")
        cookie = headers["Cookie"]
        assert "buvid4=B4" in cookie
        assert "b_nut=777" in cookie
        assert "bili_ticket=TK" in cookie
        assert "SESSDATA=S" in cookie
        assert "bili_jct=J" in cookie
        # UA must remain a browser string (no curl/python)
        assert "curl" not in headers["User-Agent"].lower()
        assert "python" not in headers["User-Agent"].lower()

    def test_explicit_buvid3_overrides_cached(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_fingerprint_path", lambda: tmp_path / "fp.json")
        save_fingerprint(Fingerprint(buvid3="CACHED"))
        headers = _bilibili_headers(sessdata="S", bili_jct="J", buvid3="EXPLICIT")
        assert "buvid3=EXPLICIT" in headers["Cookie"]
        assert "buvid3=CACHED" not in headers["Cookie"]

    def test_no_fingerprint_file_still_works(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ba, "_fingerprint_path", lambda: tmp_path / "absent.json")
        headers = _bilibili_headers(sessdata="S", bili_jct="J", buvid3="B3")
        assert "SESSDATA=S" in headers["Cookie"]
        assert "buvid3=B3" in headers["Cookie"]
