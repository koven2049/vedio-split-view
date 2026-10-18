"""Thumbnail resize helper: big source images must shrink, small ones stay."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from video_split.service.downloader import _THUMBNAIL_MAX_WIDTH, _resize_thumbnail


def _write_jpeg(path: Path, width: int, height: int) -> int:
    img = Image.new("RGB", (width, height), color=(120, 40, 40))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    path.write_bytes(buf.getvalue())
    return path.stat().st_size


def test_resize_shrinks_oversized_jpeg(tmp_path):
    p = tmp_path / "youtube_x.jpg"
    before = _write_jpeg(p, 1920, 1080)

    _resize_thumbnail(p)

    with Image.open(p) as img:
        assert img.width == _THUMBNAIL_MAX_WIDTH
        assert img.height == round(1080 * _THUMBNAIL_MAX_WIDTH / 1920)
    assert p.stat().st_size < before


def test_resize_keeps_small_image_untouched(tmp_path):
    p = tmp_path / "youtube_small.jpg"
    _write_jpeg(p, 480, 270)
    before = p.read_bytes()

    _resize_thumbnail(p)

    assert p.read_bytes() == before


def test_resize_keeps_png_format(tmp_path):
    p = tmp_path / "xiaoyuzhou_x.png"
    Image.new("RGB", (1280, 720), color=(30, 30, 30)).save(p, format="PNG")

    _resize_thumbnail(p)

    with Image.open(p) as img:
        assert img.format == "PNG"
        assert img.width == _THUMBNAIL_MAX_WIDTH


def test_resize_keeps_original_on_corrupt_file(tmp_path):
    p = tmp_path / "broken.jpg"
    p.write_bytes(b"not an image at all")
    before = p.read_bytes()

    _resize_thumbnail(p)  # must not raise

    assert p.read_bytes() == before
