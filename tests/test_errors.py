
from backend.main import _short_error

FFMPEG_DUMP = (
    "FFmpeg keyframe extraction failed: ffmpeg version 8.1.2-essentials_build\n"
    "  built with gcc 16.1.0\n"
    "  configuration: --enable-gpl --enable-version3 --enable-static "
    "--disable-w32threads --disable-autodetect --enable-cairo\n"
) * 12 + "Error opening output files: Invalid argument\n"


def test_short_error_keeps_head_and_tail():

    out = _short_error(FFMPEG_DUMP, limit=200)
    assert len(out) < len(FFMPEG_DUMP)
    assert out.startswith("FFmpeg keyframe extraction failed")
    assert out.rstrip().endswith("Error opening output files: Invalid argument")


def test_short_error_marks_how_much_was_dropped():
    out = _short_error("x" * 1000, limit=200)
    assert "略去 800 字" in out


def test_short_error_leaves_short_messages_alone():
    assert _short_error("boom") == "boom"
    assert _short_error("  boom  ") == "boom"


def test_short_error_handles_empty():
    assert _short_error("") == ""
    assert _short_error(None) == ""
