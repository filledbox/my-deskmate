import asyncio
import subprocess

import pytest
from fastapi import HTTPException

from backend import ingest


def _completed(cmd, code=0, stderr=""):
    return subprocess.CompletedProcess(cmd, code, stdout="", stderr=stderr)


def _clear_ytdlp_env(monkeypatch):
    for name in (
        "YT_DLP_CHUNK_SIZE",
        "YT_DLP_COOKIES",
        "YT_DLP_COOKIES_FROM_BROWSER",
        "YT_DLP_EXTRA_ARGS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_bilibili_downloads_use_chunked_range_by_default(monkeypatch):
    _clear_ytdlp_env(monkeypatch)
    assert ingest.chunk_size_for("https://www.bilibili.com/video/BV1xx") == "1M"
    assert ingest.chunk_size_for("https://b23.tv/abc") == "1M"


def test_other_sites_keep_ytdlp_default(monkeypatch):
    _clear_ytdlp_env(monkeypatch)
    assert ingest.chunk_size_for("https://www.youtube.com/watch?v=x") == ""


def test_chunk_size_env_override_and_disable(monkeypatch):
    monkeypatch.setenv("YT_DLP_CHUNK_SIZE", "2M")
    assert ingest.chunk_size_for("https://www.youtube.com/watch?v=x") == "2M"
    monkeypatch.setenv("YT_DLP_CHUNK_SIZE", "")
    assert ingest.chunk_size_for("https://www.bilibili.com/video/BV1xx") == ""


def test_error_summary_maps_567_to_risk_control_hint():
    stderr = (
        "ERROR: \r[download] Got error: HTTP Error 567: Unknown Status. "
        "Giving up after 10 retries\n"
    )
    message = ingest.summarize_ytdlp_error(stderr)
    assert "567" in message
    assert "风控" in message
    assert "\r" not in message and "\n" not in message


def test_error_summary_keeps_generic_error():
    message = ingest.summarize_ytdlp_error("ERROR: Unsupported URL: https://example.com/x")
    assert "Unsupported URL" in message


def test_cookies_from_browser_env(monkeypatch):
    _clear_ytdlp_env(monkeypatch)
    monkeypatch.setenv("YT_DLP_COOKIES_FROM_BROWSER", "chrome")
    assert ingest._build_extra_args(None, None) == ["--cookies-from-browser", "chrome"]


def test_download_audio_passes_chunk_size(tmp_path, monkeypatch):
    _clear_ytdlp_env(monkeypatch)
    monkeypatch.setattr(ingest, "AUDIO_DIR", str(tmp_path))
    seen = []

    async def fake_run(cmd):
        seen.append(cmd)
        (tmp_path / "v1.m4a").write_bytes(b"audio")
        return _completed(cmd)

    monkeypatch.setattr(ingest, "_run_subprocess", fake_run)
    path = asyncio.run(
        ingest.download_audio("https://www.bilibili.com/video/BV1xx", "v1")
    )

    assert path == str(tmp_path / "v1.m4a")
    cmd = seen[0]
    assert cmd[cmd.index("--http-chunk-size") + 1] == "1M"


def test_download_audio_surfaces_risk_control(tmp_path, monkeypatch):
    _clear_ytdlp_env(monkeypatch)
    monkeypatch.setattr(ingest, "AUDIO_DIR", str(tmp_path))

    async def fake_run(cmd):
        return _completed(
            cmd, 1,
            "ERROR: \r[download] Got error: HTTP Error 567: Unknown Status. "
            "Giving up after 10 retries",
        )

    monkeypatch.setattr(ingest, "_run_subprocess", fake_run)
    with pytest.raises(HTTPException) as info:
        asyncio.run(
            ingest.download_audio("https://www.bilibili.com/video/BV1xx", "v1")
        )
    assert "567" in info.value.detail
    assert "风控" in info.value.detail


def test_pipeline_falls_back_to_lowres_video_when_audio_blocked(tmp_db, tmp_path, monkeypatch):
    from backend import main, memory

    memory.insert_video(
        "fb1", "兜底课", "bilibili", "https://www.bilibili.com/video/BV1Fallback", ""
    )
    monkeypatch.setattr(main, "AUDIO_DIR", str(tmp_path))
    lowres = tmp_path / "low.mp4"
    lowres.write_bytes(b"fake-video")

    async def blocked_audio(*_args, **_kwargs):
        raise HTTPException(500, "yt-dlp 下载音频失败：B 站 CDN 触发风控（HTTP 567）")

    async def fake_lowres(*_args, **_kwargs):
        return str(lowres)

    async def fake_transcribe(*_args, **_kwargs):
        return 0.0

    def fake_extract_audio(_path, output_path=None):
        out = tmp_path / "fb1.wav"
        out.write_bytes(b"fake-audio")
        return str(out)

    monkeypatch.setattr(main, "download_audio", blocked_audio)
    monkeypatch.setattr(main, "download_lowres_video", fake_lowres)
    monkeypatch.setattr(main, "transcribe_progressively", fake_transcribe)
    monkeypatch.setattr(main, "extract_audio", fake_extract_audio)
    monkeypatch.setattr(main, "extract_keyframes", lambda *_a, **_k: [])
    monkeypatch.setattr(main, "count_keyframes", lambda _vid: 0)
    monkeypatch.setattr(main, "get_video_duration", lambda _path: 42.0)

    asyncio.run(main.run_preprocessing_pipeline("fb1"))

    row = memory.get_video("fb1")
    assert row["status"] == "ready", row["error_message"]
    assert row["file_path"] == str(lowres)
    assert (tmp_path / "fb1.wav").exists()


def test_pipeline_keeps_audio_error_when_fallback_also_fails(tmp_db, tmp_path, monkeypatch):
    from backend import main, memory

    memory.insert_video(
        "fb2", "失败课", "bilibili", "https://www.bilibili.com/video/BV1Fallback2", ""
    )
    monkeypatch.setattr(main, "AUDIO_DIR", str(tmp_path))

    async def blocked_audio(*_args, **_kwargs):
        raise HTTPException(500, "yt-dlp 下载音频失败：B 站 CDN 触发风控（HTTP 567）")

    async def failed_lowres(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main, "download_audio", blocked_audio)
    monkeypatch.setattr(main, "download_lowres_video", failed_lowres)

    asyncio.run(main.run_preprocessing_pipeline("fb2"))

    row = memory.get_video("fb2")
    assert row["status"] == "error"
    assert "567" in row["error_message"]
    assert "风控" in row["error_message"]
