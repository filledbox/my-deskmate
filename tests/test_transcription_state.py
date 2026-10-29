import asyncio

import pytest
from fastapi import HTTPException

from backend import main, memory, messages
from backend.asr import (
    ASRAudioEnded,
    ASRConfigError,
    ASRQuotaExhaustedError,
    ASRWindowError,
)


def _insert(video_id: str, duration: float = 100.0):
    memory.insert_video(
        video_id, "测试课", "bilibili",
        f"https://www.bilibili.com/video/{video_id}", "",
        duration_seconds=duration,
    )


def _mock_pipeline(monkeypatch, tmp_path, duration: float = 100.0, lowres: bool = True):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"fake")

    async def fake_audio(*_args, **_kwargs):
        return str(audio)

    async def fake_lowres(*_args, **_kwargs):
        if not lowres:
            return None
        path = tmp_path / "v.mp4"
        path.write_bytes(b"fake")
        return str(path)

    async def fake_chapters(*_args, **_kwargs):
        return []

    monkeypatch.setattr(main, "download_audio", fake_audio)
    monkeypatch.setattr(main, "download_lowres_video", fake_lowres)
    monkeypatch.setattr(main, "get_video_duration", lambda _path: duration)
    monkeypatch.setattr(main, "extract_keyframes", lambda *_a, **_k: [])
    monkeypatch.setattr(main, "segment_into_chapters", fake_chapters)
    monkeypatch.setattr(main, "AUDIO_DIR", str(tmp_path))


def test_audio_ended_only_advances_coverage(tmp_db, monkeypatch):
    async def fake_window(*_args, **_kwargs):
        raise ASRAudioEnded(0, 100, "empty")

    monkeypatch.setattr(main, "transcribe_window", fake_window)
    coverage = asyncio.run(main.transcribe_progressively("v1", "fake.m4a", 100))

    assert coverage == 100
    assert memory.get_latest_progress("v1")["stage"] == "transcription_warning"


def test_non_transient_error_is_not_treated_as_no_audio(tmp_db, monkeypatch):
    async def fake_window(*_args, **_kwargs):
        raise ASRWindowError(0, 100, "HTTP 400: bad", transient=False)

    monkeypatch.setattr(main, "transcribe_window", fake_window)
    with pytest.raises(ASRWindowError):
        asyncio.run(main.transcribe_progressively("v1", "fake.m4a", 100))

    assert memory.get_transcription_coverage("v1") == 0.0


def test_first_window_failure_marks_error_with_friendly_text(tmp_db, tmp_path, monkeypatch):
    _insert("t3")
    _mock_pipeline(monkeypatch, tmp_path)

    async def fake_window(*_args, **_kwargs):
        raise ASRWindowError(0, 100, "HTTP 400: bad", transient=False)

    monkeypatch.setattr(main, "transcribe_window", fake_window)
    asyncio.run(main.run_preprocessing_pipeline("t3"))

    row = memory.get_video("t3")
    assert row["status"] == "error"
    assert "转写服务" in row["error_message"]
    assert "HTTP 400" in memory.get_latest_progress("t3")["details"]


def test_partial_transcript_stays_ready_and_incomplete(tmp_db, tmp_path, monkeypatch, no_chroma):
    _insert("t4", duration=200)
    _mock_pipeline(monkeypatch, tmp_path, duration=200)
    monkeypatch.setattr(main, "TRANSCRIBE_WINDOW_SECONDS", 100)
    monkeypatch.setattr(main, "TRANSCRIBE_OVERLAP_SECONDS", 0)
    calls = {"n": 0}

    async def fake_window(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"start": 0, "end": 10, "text": "前半段"}]
        raise ASRWindowError(100, 200, "HTTP 400: bad", transient=False)

    monkeypatch.setattr(main, "transcribe_window", fake_window)
    asyncio.run(main.run_preprocessing_pipeline("t4"))

    row = memory.get_video("t4")
    assert row["status"] == "ready"
    assert row["error_message"] is None
    assert memory.get_latest_progress("t4")["stage"] == "transcription_incomplete"
    assert len(memory.get_all_transcript_segments("t4")) == 1


def test_silent_video_is_ready_with_no_speech(tmp_db, tmp_path, monkeypatch):
    _insert("t5")
    _mock_pipeline(monkeypatch, tmp_path)

    async def fake_window(*_args, **_kwargs):
        raise ASRAudioEnded(0, 100, "empty")

    monkeypatch.setattr(main, "transcribe_window", fake_window)
    asyncio.run(main.run_preprocessing_pipeline("t5"))

    row = memory.get_video("t5")
    assert row["status"] == "ready"
    assert row["error_message"] is None
    progress = memory.get_latest_progress("t5")
    assert progress["stage"] == "no_speech"
    assert "没有讲解声" in progress["details"]
    assert memory.get_all_transcript_segments("t5") == []


def test_silent_video_with_frames_is_visual_only(tmp_db, tmp_path, monkeypatch):
    _insert("t6")
    _mock_pipeline(monkeypatch, tmp_path)

    async def fake_window(*_args, **_kwargs):
        raise ASRAudioEnded(0, 100, "empty")

    monkeypatch.setattr(main, "transcribe_window", fake_window)
    monkeypatch.setattr(main, "count_keyframes", lambda _vid: 1)
    asyncio.run(main.run_preprocessing_pipeline("t6"))

    progress = memory.get_latest_progress("t6")
    assert progress["stage"] == "no_speech"
    assert "根据画面" in progress["details"]


def test_lowres_failure_records_keyframes_skipped(tmp_db, tmp_path, monkeypatch, no_chroma):
    _insert("t7")
    _mock_pipeline(monkeypatch, tmp_path, lowres=False)

    async def fake_window(*_args, **_kwargs):
        return [{"start": 0, "end": 10, "text": "有内容"}]

    monkeypatch.setattr(main, "transcribe_window", fake_window)
    asyncio.run(main.run_preprocessing_pipeline("t7"))

    row = memory.get_video("t7")
    assert row["status"] == "ready"
    progress = memory.get_latest_progress("t7")
    assert progress["stage"] == "keyframes_skipped"
    assert "画面" in progress["details"]
    assert len(memory.get_all_transcript_segments("t7")) == 1


def test_progress_payload_keeps_messages_on_reload():
    error_video = {"status": "error", "error_message": messages.NO_SPEECH,
                   "duration_seconds": 10}
    payload = main._progress_payload(
        error_video, {"stage": "error", "processed_up_to_seconds": 0}
    )
    assert payload["message"] == messages.NO_SPEECH

    ready_video = {"status": "ready", "error_message": None, "duration_seconds": 10}
    payload = main._progress_payload(
        ready_video,
        {"stage": "transcription_incomplete", "processed_up_to_seconds": 5,
         "details": messages.INCOMPLETE},
    )
    assert payload["message"] == messages.INCOMPLETE


def test_friendly_error_mapping():
    assert "设置" in messages.friendly_error(ASRConfigError("ASR 未配置或只配了一半"))
    assert "额度" in messages.friendly_error(ASRQuotaExhaustedError("额度已耗尽"))
    assert "B 站" in messages.friendly_error(
        HTTPException(500, "yt-dlp 下载失败：HTTP Error 567")
    )
    assert "音频处理" in messages.friendly_error(
        RuntimeError("FFmpeg keyframe extraction failed")
    )
    assert "转写服务" in messages.friendly_error(
        ASRWindowError(0, 1, "HTTP 400: bad")
    )
    assert messages.friendly_error(
        HTTPException(500, "Downloaded audio not found")
    ) == messages.GENERIC_ERROR
    assert messages.friendly_error(ValueError("???")) == messages.GENERIC_ERROR
    assert "B 站" in messages.friendly_stored(
        "500: yt-dlp failed: ERROR: HTTP Error 567"
    )
    assert "设置" in messages.friendly_stored("ASR 未配置或只配了一半：缺 ASR_BASE_URL")
    assert messages.friendly_stored(messages.NO_SPEECH) == messages.NO_SPEECH
