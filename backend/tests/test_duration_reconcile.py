"""Platform durations that are rounded down must not truncate the video.

小宇宙 publishes JSON-LD ``timeRequired`` in whole minutes (``PT14M`` for a
14:31 episode), so a stored duration can be up to 59s short. Segment bounds
and the progress bar are derived from it, so the tail of every episode got
cut off. The transcript is ground truth for "audio that exists".
"""
from __future__ import annotations

from video_split.service.downloader import SubtitleEntry, VideoMeta
from video_split.service.video_service import _reconcile_duration


def _meta(duration: int) -> VideoMeta:
    return VideoMeta(
        url="https://www.xiaoyuzhoufm.com/episode/abc",
        platform="xiaoyuzhou",
        video_id="abc",
        title="ep",
        duration_seconds=duration,
        thumbnail_url="",
    )


def _subs(*spans: tuple[float, float]) -> list[SubtitleEntry]:
    return [SubtitleEntry(start=s, duration=d, text="x") for s, d in spans]


def test_grows_duration_to_cover_transcript_tail():
    """PT14M (840s) episode whose speech runs to 871s must report 871s."""
    assert _reconcile_duration(_meta(840), _subs((860.0, 11.0))) == 871


def test_never_shrinks_on_trailing_silence():
    """Music/silence after the last words is real video — keep the longer value."""
    assert _reconcile_duration(_meta(3600), _subs((100.0, 5.0))) == 3600


def test_empty_transcript_leaves_duration_untouched():
    assert _reconcile_duration(_meta(900), []) == 900


def test_uses_max_end_not_last_entry():
    """Out-of-order or overlapping entries must not lose the true end."""
    assert _reconcile_duration(_meta(100), _subs((150.0, 10.0), (120.0, 5.0))) == 160


def test_exact_duration_is_a_no_op():
    assert _reconcile_duration(_meta(500), _subs((490.0, 10.0))) == 500
