"""Usage ledger: every billed ASR / LLM call is recorded and costed.

WHY: the library "累计消耗" panel summed only per-video analysis usage, so
dialogue / mind-map / translation calls, retries, resumed chunks and deleted
videos were missing (glm-5.3-flash showed 1.59M tokens vs 2.28M in llm_logs).
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.conftest import admin_create_user, get_admin_token
from video_split.models import UsageRecord
from video_split.service import llm_http, usage


@pytest.fixture
def ledger_db(db_engine, monkeypatch):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("video_split.database._get_session_factory", lambda: factory)
    usage.set_owner(None)
    return factory


class _User:
    def __init__(self, uid: int, role: str = "admin"):
        self.id = uid
        self.role = role


@pytest.mark.asyncio
async def test_records_carry_owner_and_summary_prices_them(ledger_db):
    usage.set_owner(1, 42)
    # qwen-audio-3.1: token-billed (live GTA6 episode numbers)
    await usage.record("asr", "qwen-audio-3.1-asr-flash-filetrans", seconds=4174,
                       prompt_tokens=54486, completion_tokens=15083, total_tokens=69569)
    await usage.record("asr", "fun-asr", seconds=3600)
    await usage.record_llm_response(
        "glm-5.3-flash", {"usage": {"prompt_tokens": 1000, "completion_tokens": 200}}
    )

    async with ledger_db() as db:
        rows = (await db.execute(UsageRecord.__table__.select())).all()
        assert {(r.user_id, r.task_id) for r in rows} == {(1, 42)}
        summary = await usage.summarize(db, _User(1))

    asr = {a["model"]: a for a in summary["asr"]}
    assert asr["qwen-audio-3.1-asr-flash-filetrans"]["cost"] == pytest.approx(0.0843, abs=1e-4)
    assert asr["fun-asr"]["cost"] == pytest.approx(0.792)
    assert asr["fun-asr"]["total_seconds"] == 3600
    llm = summary["llm"][0]
    # LLM runs on a subscription endpoint in production: no default price
    assert (llm["model"], llm["total_tokens"], llm["cost"]) == ("glm-5.3-flash", 1200, None)
    assert summary["total_cost"] == pytest.approx(0.0843 + 0.792, abs=1e-4)


@pytest.mark.asyncio
async def test_config_price_override_applies(ledger_db, monkeypatch):
    from video_split.config import ModelPrice, get_settings

    monkeypatch.setitem(get_settings().pricing, "glm-5.3-flash",
                        ModelPrice(input_per_m=0.8, output_per_m=2.8))
    await usage.record("llm", "glm-5.3-flash", prompt_tokens=1_000_000,
                       completion_tokens=1_000_000, total_tokens=2_000_000)
    async with ledger_db() as db:
        summary = await usage.summarize(db, _User(1))
    assert summary["llm"][0]["cost"] == pytest.approx(3.6)


@pytest.mark.asyncio
async def test_unattributed_rows_count_for_admin_only(ledger_db):
    await usage.record("asr", "fun-asr", seconds=10)  # no owner
    usage.set_owner(2)
    await usage.record("asr", "fun-asr", seconds=5)
    async with ledger_db() as db:
        admin = await usage.summarize(db, _User(1, "admin"))
        viewer = await usage.summarize(db, _User(2, "viewer"))
    assert admin["asr"][0]["total_seconds"] == 10
    assert viewer["asr"][0]["total_seconds"] == 5


@pytest.mark.asyncio
async def test_post_chat_records_every_successful_call(ledger_db, monkeypatch):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kw):
            return httpx.Response(
                200, request=httpx.Request("POST", url),
                json={"choices": [{"message": {"content": "{}"}}],
                      "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}},
            )

    monkeypatch.setattr(llm_http.httpx, "AsyncClient", lambda **kw: _Client())
    usage.set_owner(1)
    for _ in range(2):
        await llm_http.post_chat("https://llm/chat/completions", {"model": "glm-x"}, {},
                                 httpx.Timeout(5))
    async with ledger_db() as db:
        summary = await usage.summarize(db, _User(1))
    assert summary["llm"] == [{
        "model": "glm-x", "requests": 2, "cost": None,
        "prompt_tokens": 14, "completion_tokens": 6, "total_tokens": 20,
    }]


@pytest.mark.asyncio
async def test_record_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("video_split.database._get_session_factory", _boom)
    await usage.record("llm", "m", total_tokens=1)  # must not raise


@pytest.mark.asyncio
async def test_usage_endpoints_read_the_ledger(client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as db:
        db.add(UsageRecord(user_id=1, kind="asr", model="fun-asr", seconds=7200))
        db.add(UsageRecord(user_id=1, kind="llm", model="glm-5.3-flash",
                           prompt_tokens=80, completion_tokens=20, total_tokens=100))
        await db.commit()

    headers = {"Authorization": f"Bearer {await get_admin_token(client)}"}
    summary = (await client.get("/api/videos/usage-summary", headers=headers)).json()
    assert summary["asr"] == [
        {"model": "fun-asr", "requests": 1, "cost": pytest.approx(1.584), "total_seconds": 7200}
    ]
    assert summary["total_cost"] == pytest.approx(1.584)

    stats = (await client.get("/api/auth/usage-stats", headers=headers)).json()
    assert stats["asr"] == {"fun-asr": {"total_seconds": 7200, "requests": 1}}
    assert stats["llm"]["glm-5.3-flash"]["total_tokens"] == 100

    viewer = await admin_create_user(client, "viewer1")
    empty = (await client.get("/api/videos/usage-summary",
                              headers={"Authorization": f"Bearer {viewer}"})).json()
    assert empty == {"asr": [], "llm": [], "total_cost": None}


def _load_backfill():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "backfill_usage.py"
    spec = importlib.util.spec_from_file_location("backfill_usage", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
async def test_backfill_takes_only_pre_ledger_history_once(db_engine, capsys):
    import json as _json
    import sqlite3
    from datetime import datetime as _dt

    from video_split.models import LLMLog, User, Video

    def ts(text):
        return _dt.strptime(text, "%Y-%m-%d %H:%M:%S")

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as db:
        db.add(User(id=1, username="admin", password_hash="x", role="admin"))

        def video(vid, created, **u):
            db.add(Video(id=vid, user_id=1, platform="xiaoyuzhou", video_id=f"v{vid}",
                         url="u", title="t", usage_json=_json.dumps(u), created_at=ts(created)))

        # before llm_logs existed: ASR + LLM from usage_json
        video(1, "2026-07-01 00:00:00", asr_model="fun-asr", asr_duration_seconds=100,
              llm_model="glm-5.2", llm_prompt_tokens=8, llm_completion_tokens=2,
              llm_total_tokens=10)
        # after llm_logs start: ASR from usage_json, LLM only from llm_logs
        video(2, "2026-08-01 00:00:00", asr_model="fun-asr", asr_duration_seconds=50,
              llm_model="glm-5.2", llm_total_tokens=999)
        # after the live ledger started: not backfilled at all
        video(3, "2026-12-01 00:00:00", asr_model="fun-asr", asr_duration_seconds=7)
        for created, tokens in (("2026-07-27 00:00:00", 30), ("2026-08-02 00:00:00", 0),
                                ("2026-12-02 00:00:00", 40)):
            db.add(LLMLog(model="glm-5.3-flash", purpose="dialogue", total_tokens=tokens,
                          created_at=ts(created)))
        db.add(UsageRecord(kind="asr", model="fun-asr", seconds=1, source="live",
                           created_at=ts("2026-11-20 00:00:00")))
        await db.commit()

    db_path = db_engine.url.database
    con = sqlite3.connect(db_path)
    mod = _load_backfill()
    assert mod.main(["--db", db_path, "--dry-run"]) == 0
    assert con.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0] == 1

    assert mod.main(["--db", db_path]) == 0
    rows = con.execute(
        "SELECT user_id, kind, model, seconds, total_tokens FROM usage_records "
        "WHERE source = 'backfill' ORDER BY created_at"
    ).fetchall()
    assert rows == [
        (1, "asr", "fun-asr", 100.0, 0),
        (1, "llm", "glm-5.2", 0.0, 10),
        (1, "llm", "glm-5.3-flash", 0.0, 30),
        (1, "asr", "fun-asr", 50.0, 0),
    ]
    assert mod.main(["--db", db_path]) == 0  # second run is a no-op
    assert "already backfilled" in capsys.readouterr().out
    con.close()


def test_backfill_help_is_side_effect_free(capsys):
    mod = _load_backfill()
    with pytest.raises(SystemExit) as exc:
        mod.main(["--help"])
    assert exc.value.code == 0
    assert "usage: backfill_usage.py" in capsys.readouterr().out
