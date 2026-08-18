"""Repair video durations that a platform reported rounded down to the minute.

小宇宙 exposes JSON-LD ``timeRequired`` as whole minutes (``PT14M`` for a
14:31 episode), so ``videos.duration_seconds`` can be up to 59s short. The
progress bar and the last segment's ``end_seconds`` are derived from it, so the
tail of every affected episode was unreachable in the player.

The stored transcript is ground truth: audio that produced words is audio that
exists. This recomputes duration from ``subtitle_json`` — no re-transcription,
no ASR spend — and widens the final segment to match.

Only ever grows a duration. A transcript ending before the stated duration is
normal (trailing music/silence) and is left alone.

Usage:
    python scripts/fix_truncated_durations.py            # dry run
    python scripts/fix_truncated_durations.py --apply
    python scripts/fix_truncated_durations.py --apply --ids 3,5,14
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select

from video_split.database import _get_session_factory
from video_split.models import Segment, Video


def _transcript_end(subtitle_json: str) -> float | None:
    """Latest end timestamp across all subtitle entries, or None if unusable."""
    if not subtitle_json:
        return None
    try:
        entries = json.loads(subtitle_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(entries, list) or not entries:
        return None
    ends = [
        float(e.get("start", 0)) + float(e.get("duration", 0))
        for e in entries
        if isinstance(e, dict)
    ]
    return max(ends) if ends else None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--ids", default="", help="comma-separated video ids to limit to")
    args = ap.parse_args()

    only = {int(x) for x in args.ids.split(",") if x.strip()} if args.ids else None

    factory = _get_session_factory()
    async with factory() as session:
        videos = (await session.execute(select(Video).order_by(Video.id))).scalars().all()

        planned: list[tuple[Video, int, float]] = []
        for v in videos:
            if only and v.id not in only:
                continue
            end = _transcript_end(v.subtitle_json)
            if end is None:
                continue
            corrected = int(round(end))
            if corrected > v.duration_seconds:
                planned.append((v, corrected, end))

        if not planned:
            print("Nothing to fix — every duration already covers its transcript.")
            return 0

        print(f"{'APPLY' if args.apply else 'DRY RUN'} — {len(planned)} video(s) to widen\n")
        print(f"{'id':>4}  {'stored':>7}  {'actual':>7}  {'delta':>6}  {'%60':>4}  title")
        print("-" * 78)

        for v, corrected, end in planned:
            delta = corrected - v.duration_seconds
            print(
                f"{v.id:>4}  {v.duration_seconds:>7}  {corrected:>7}  "
                f"{delta:>+6}  {v.duration_seconds % 60:>4}  {v.title[:34]}"
            )

            if not args.apply:
                continue

            v.duration_seconds = corrected

            # The last segment's end_seconds came from the truncated duration;
            # stretch it so the player can reach the tail. Earlier segment
            # bounds are LLM topic decisions and stay untouched.
            segs = (
                await session.execute(
                    select(Segment)
                    .where(Segment.video_id == v.id)
                    .order_by(Segment.segment_index)
                )
            ).scalars().all()
            if segs and segs[-1].end_seconds < corrected:
                segs[-1].end_seconds = corrected

        if args.apply:
            await session.commit()
            print(f"\nCommitted {len(planned)} video(s).")
        else:
            print("\nDry run — nothing written. Re-run with --apply.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
