"""Export/import round-trip coverage for the newer Video columns.

Regression test: ``_video_to_dict`` and ``import_videos`` once silently
dropped dialogue/takeaways/show_intro/uploader (and essence) — an
export→import backup cycle lost them permanently, with no endpoint able
to back-fill takeaways/show_intro/uploader afterwards.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from video_split.models import User, Video
from video_split.service import data_sync

_EXPORTED_FIELDS = [
    "essence",
    "uploader",
    "dialogue_json",
    "takeaways_json",
    "show_intro",
]


@pytest.mark.asyncio
async def test_export_import_round_trips_new_columns(client, db_session, tmp_path, monkeypatch):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr(data_sync, "EXPORTS_DIR", export_dir)

    admin = (await db_session.execute(select(User).where(User.username == "admin"))).scalar_one()
    video = Video(
        user_id=admin.id,
        url="https://www.xiaoyuzhoufm.com/episode/rt1",
        platform="xiaoyuzhou",
        video_id="rt1",
        title="round trip",
        duration_seconds=60,
        summary="s",
        essence="精华",
        uploader="某频道",
        subtitle_json=json.dumps([{"start": 0, "duration": 5, "text": "你好"}]),
        dialogue_json=json.dumps({"speakers": [], "blocks": [], "sections": [], "fallback": True}),
        takeaways_json=json.dumps([{"title": "t", "body": "b", "highlight": "h"}]),
        show_intro="节目简介",
    )
    db_session.add(video)
    await db_session.commit()
    # _video_to_dict iterates video.segments — load the relationship explicitly
    # (lazy load inside an async session raises MissingGreenlet).
    await db_session.refresh(video, ["segments"])

    exported = data_sync._video_to_dict(video, [])
    for field in _EXPORTED_FIELDS:
        assert field in exported, f"_video_to_dict dropped {field}"
    assert exported["uploader"] == "某频道"

    # Round-trip through the real import path: export file on disk, original
    # row removed so the platform+video_id dedup doesn't skip the import.
    path = export_dir / data_sync._export_filename(video.platform, video.video_id)
    path.write_text(json.dumps(exported, ensure_ascii=False), encoding="utf-8")
    await db_session.delete(video)
    await db_session.commit()
    result = await data_sync.import_videos(db_session, admin.id)
    assert result["imported"] == 1

    imported = (
        await db_session.execute(
            select(Video).where(Video.platform == "xiaoyuzhou", Video.video_id == "rt1")
        )
    ).scalar_one()
    assert imported.essence == "精华"
    assert imported.uploader == "某频道"
    assert json.loads(imported.dialogue_json)["fallback"] is True
    assert json.loads(imported.takeaways_json)[0]["title"] == "t"
    assert imported.show_intro == "节目简介"
