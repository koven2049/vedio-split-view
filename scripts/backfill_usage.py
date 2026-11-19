#!/usr/bin/env python3
"""Backfill the usage ledger (usage_records) from pre-ledger history.

Run once, after the service has started at least once with the ledger (that
start creates the table). Only inserts rows into usage_records; never changes
other tables. Safe to re-run: exits without writing when backfill rows exist.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

DESCRIPTION = """\
Reconstruct usage-ledger rows for calls made before the ledger existed, so
the "累计消耗" panel covers history. Sources: ASR seconds from each video's
usage_json; LLM tokens from llm_logs (all purposes, attributed to the admin
account) plus usage_json for videos older than the first llm_logs row.
Only rows older than the first live ledger row are taken, so nothing is
counted twice. Lost for good: deleted videos' ASR, failed/retried ASR tasks,
mind-map calls and translation tokens (never logged).
"""

EPILOG = """\
examples:
  backend/.venv/bin/python scripts/backfill_usage.py --dry-run
  backend/.venv/bin/python scripts/backfill_usage.py --db data/video_split.db
"""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backfill_usage.py",
        usage="%(prog)s [-h] [--db PATH] [--dry-run]",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db", metavar="PATH", default="data/video_split.db",
        help="SQLite database (default: data/video_split.db)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print what would be inserted; write nothing",
    )
    return parser.parse_args(argv)


def _plan(con: sqlite3.Connection) -> list[tuple]:
    live_start = con.execute(
        "SELECT MIN(created_at) FROM usage_records WHERE source = 'live'"
    ).fetchone()[0]
    cutoff = live_start or datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    first_log = con.execute("SELECT MIN(created_at) FROM llm_logs").fetchone()[0] or cutoff
    llm_video_cutoff = min(first_log, cutoff)
    admin = con.execute(
        "SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
    ).fetchone()
    admin_id = admin[0] if admin else None

    rows: list[tuple] = []
    for user_id, raw, created in con.execute(
        "SELECT user_id, usage_json, created_at FROM videos WHERE created_at < ?", (cutoff,)
    ):
        try:
            u = json.loads(raw or "{}")
        except json.JSONDecodeError:
            continue
        if u.get("asr_model") and float(u.get("asr_duration_seconds") or 0) > 0:
            rows.append((user_id, "asr", u["asr_model"], float(u["asr_duration_seconds"]),
                         0, 0, 0, created))
        if created < llm_video_cutoff and u.get("llm_model") and int(u.get("llm_total_tokens") or 0):
            rows.append((user_id, "llm", u["llm_model"], 0.0,
                         int(u.get("llm_prompt_tokens") or 0),
                         int(u.get("llm_completion_tokens") or 0),
                         int(u["llm_total_tokens"]), created))
    for model, p, c, t, created in con.execute(
        "SELECT model, prompt_tokens, completion_tokens, total_tokens, created_at "
        "FROM llm_logs WHERE created_at < ? AND total_tokens > 0", (cutoff,)
    ):
        rows.append((admin_id, "llm", model, 0.0, p, c, t, created))
    return rows


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    db = Path(args.db)
    if not db.exists():
        print(f"error: database not found: {db}", file=sys.stderr)
        return 1
    con = sqlite3.connect(db)
    try:
        has_table = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'usage_records'"
        ).fetchone()
        if not has_table:
            print("error: usage_records missing — start the service once first", file=sys.stderr)
            return 1
        done = con.execute(
            "SELECT COUNT(*) FROM usage_records WHERE source = 'backfill'"
        ).fetchone()[0]
        if done:
            print(f"already backfilled ({done} rows); nothing to do")
            return 0

        rows = _plan(con)
        totals: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0.0, 0])
        for _uid, kind, model, seconds, _p, _c, tokens, _ts in rows:
            acc = totals[(kind, model)]
            acc[0] += 1
            acc[1] += seconds
            acc[2] += tokens
        for (kind, model), (n, seconds, tokens) in sorted(totals.items()):
            amount = f"{seconds / 3600:.1f} h" if kind == "asr" else f"{tokens:,} tokens"
            print(f"{kind:3}  {model:40} {n:4} rows  {amount}")
        if args.dry_run:
            print(f"dry run: {len(rows)} rows would be inserted")
            return 0
        con.executemany(
            "INSERT INTO usage_records (user_id, kind, model, seconds, prompt_tokens, "
            "completion_tokens, total_tokens, created_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'backfill')",
            rows,
        )
        con.commit()
        print(f"inserted {len(rows)} rows")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
