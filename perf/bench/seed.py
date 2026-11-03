"""Seed a throwaway bench database shaped like production (2026-11-03 snapshot).

Prod shape (read-only sqlite query on ts-macmini-m4): 57 videos, summary
~190-260 chars, mindmap_json up to 11 KB, raw_transcript avg 25 KB / max
93 KB, subtitle_json avg 58 KB / max 178 KB, up to 20 segments, 3 tags.
We seed 60 videos at the *max* end so list/detail payload effects show up.

Usage (run with the backend venv, cwd = a bench dir holding config/app.yaml):
    python seed.py <thumb_src.jpg>
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from video_split.config import get_settings
from video_split.database import get_db, init_db
from video_split.service.auth_service import ensure_admin_user

N_VIDEOS = 60
N_SEGMENTS = 20


async def _init() -> None:
    await init_db()
    async for db in get_db():
        await ensure_admin_user(db)
        break


def _subtitles(n: int = 2600) -> str:
    line = "这是一段用于测速的字幕文本，长度与线上逐字稿接近，内容本身没有意义。"
    return json.dumps(
        [{"start": i * 2.5, "duration": 2.5, "text": f"{line}{i}"} for i in range(n)],
        ensure_ascii=False,
    )


def _mindmap() -> str:
    chapters = [
        {
            "title": f"第{c}章 测速章节标题",
            "title_en": f"Chapter {c}",
            "summary": "章节摘要" * 20,
            "summary_en": "chapter summary " * 10,
            "key_points": [{"text": f"要点{k}" * 6, "text_en": f"point {k}"} for k in range(5)],
            "quotes": [{"text": "金句" * 10, "text_en": "quote", "time_ref": "00:10:00"}],
        }
        for c in range(8)
    ]
    return json.dumps({"status": "ok", "chapters": chapters}, ensure_ascii=False)


def main() -> None:
    thumb_src = Path(sys.argv[1])
    settings = get_settings()
    asyncio.run(_init())

    thumb_dir = Path(settings.storage.temp_dir).parent / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    subs = _subtitles()
    transcript = "测速逐字稿" * 18000  # ~90 KB
    mindmap = _mindmap()
    takeaways = json.dumps([{"title": "要点", "body": "说明" * 30}] * 5, ensure_ascii=False)

    con = sqlite3.connect(settings.storage.db_path)
    admin_id = con.execute("select id from users where role='admin'").fetchone()[0]
    for name in ("AI", "播客", "访谈"):
        con.execute("insert or ignore into tags(name, color) values (?, '#3b82f6')", (name,))
    tag_ids = [r[0] for r in con.execute("select id from tags")]
    for i in range(1, N_VIDEOS + 1):
        thumb = f"bench_{i}.jpg"
        shutil.copy(thumb_src, thumb_dir / thumb)
        cur = con.execute(
            "insert into videos(user_id,url,platform,video_id,title,thumbnail_url,upload_date,"
            "uploader,duration_seconds,summary,summary_en,essence,raw_transcript,subtitle_json,"
            "usage_json,mindmap_json,dialogue_json,takeaways_json,show_intro,is_public)"
            " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                admin_id, f"https://www.youtube.com/watch?v=bench{i:05d}", "youtube",
                f"bench{i:05d}", f"测速视频 {i}：一个长度接近线上的标题文本",
                f"/api/thumbnails/{thumb}", "20261001", "测速频道", 6500,
                "摘要" * 120, "", "精华" * 700, transcript, subs,
                json.dumps({"asr_duration_seconds": 5413.0, "asr_model": "asr-x", "llm_prompt_tokens": 48273, "llm_completion_tokens": 5702, "llm_total_tokens": 53975, "llm_model": "llm-x"}), mindmap, "",
                takeaways, "节目简介" * 30, i % 2,
            ),
        )
        vid = cur.lastrowid
        for s in range(N_SEGMENTS):
            con.execute(
                "insert into segments(video_id,segment_index,title,title_en,summary,summary_en,"
                "start_seconds,end_seconds) values (?,?,?,?,?,?,?,?)",
                (vid, s, f"片段 {s}", "", "片段摘要" * 25, "", s * 300, (s + 1) * 300),
            )
        con.execute("insert into video_tags values (?,?)", (vid, tag_ids[i % len(tag_ids)]))
    con.commit()
    print(f"seeded {N_VIDEOS} videos into {settings.storage.db_path}")


if __name__ == "__main__":
    main()
