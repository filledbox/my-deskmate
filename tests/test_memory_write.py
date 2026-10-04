


import os

from backend import memory
from backend.memory import (
    _build_index_units,
    _window_start,
    get_all_transcript_segments,
    get_transcription_coverage,
    normalize_video_url,
    persist_transcript,
)


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_normalize_strips_trailing_slash_and_tracking_params():
    assert normalize_video_url("https://www.bilibili.com/video/BV1Test12345/") == \
        "https://www.bilibili.com/video/BV1Test12345"


def test_normalize_keeps_only_page_param():
    assert normalize_video_url(
        "https://www.bilibili.com/video/BV1Test12345?vd_source=abc&p=2") == \
        "https://www.bilibili.com/video/BV1Test12345?p=2"


def test_normalize_drops_empty_page_param():
    assert normalize_video_url("https://x.com/v?p=&a=1") == "https://x.com/v"


def test_normalize_empty_stays_empty():
    assert normalize_video_url("") == ""
    assert normalize_video_url(None) == ""


def test_normalize_is_idempotent():
    once = normalize_video_url("https://x.com/v/?p=3")
    assert normalize_video_url(once) == once


def test_window_start_aligns_down_to_60s():
    assert _window_start(0.0) == 0.0
    assert _window_start(59.9) == 0.0
    assert _window_start(60.0) == 60.0
    assert _window_start(125.4) == 120.0


def test_window_start_is_derivable_from_time_alone():

    assert _window_start(305.0) == _window_start(305.9) == 300.0


def test_build_index_units_groups_by_window():
    units = _build_index_units("v1", [
        seg(1.0, 3.0, "甲"), seg(30.0, 32.0, "乙"), seg(65.0, 67.0, "丙"),
    ], "speech")
    assert len(units) == 2
    assert units[0]["document"] == "甲乙"
    assert units[0]["start_time"] == 0.0
    assert units[0]["end_time"] == 32.0
    assert units[1]["document"] == "丙"


def test_build_index_units_id_is_deterministic():
    a = _build_index_units("v1", [seg(61.0, 62.0, "甲")], "speech")
    b = _build_index_units("v1", [seg(61.0, 62.0, "甲")], "speech")
    assert [u["id"] for u in a] == [u["id"] for u in b]


def test_persist_transcript_writes_rows_and_coverage(tmp_db, no_chroma):
    n = persist_transcript("v1", [seg(0.0, 5.0, "第一句"), seg(5.0, 9.0, "第二句")])
    assert n == 2
    assert [s["text"] for s in get_all_transcript_segments("v1")] == ["第一句", "第二句"]
    assert get_transcription_coverage("v1") == 9.0


def test_persist_transcript_replaces_overlapping_span(tmp_db, no_chroma):

    persist_transcript("v1", [seg(0.0, 5.0, "旧A"), seg(5.0, 10.0, "旧B")])
    persist_transcript("v1", [seg(0.0, 10.0, "新AB")])
    assert [s["text"] for s in get_all_transcript_segments("v1")] == ["新AB"]


def test_persist_transcript_deletes_row_straddling_the_new_span(tmp_db, no_chroma):


    persist_transcript("v1", [seg(0.0, 20.0, "跨接缝的旧行")])
    persist_transcript("v1", [seg(10.0, 30.0, "新窗口")])
    assert [s["text"] for s in get_all_transcript_segments("v1")] == ["新窗口"]


def test_persist_transcript_keeps_rows_outside_the_span(tmp_db, no_chroma):
    persist_transcript("v1", [seg(0.0, 10.0, "前段")])
    persist_transcript("v1", [seg(100.0, 110.0, "后段")])
    assert [s["text"] for s in get_all_transcript_segments("v1")] == ["前段", "后段"]


def test_persist_transcript_is_idempotent(tmp_db, no_chroma):
    segs = [seg(0.0, 5.0, "甲"), seg(5.0, 10.0, "乙")]
    persist_transcript("v1", segs)
    persist_transcript("v1", segs)
    assert len(get_all_transcript_segments("v1")) == 2


def test_persist_transcript_reindexes_the_covering_windows(tmp_db, no_chroma):

    persist_transcript("v1", [seg(10.0, 130.0, "长段")])
    assert no_chroma == [("v1", 10.0, 130.0, "speech")]


def test_persist_transcript_empty_is_a_noop(tmp_db, no_chroma):
    assert persist_transcript("v1", []) == 0
    assert get_all_transcript_segments("v1") == []
    assert no_chroma == []


def test_persist_transcript_source_type_is_respected(tmp_db, no_chroma):
    persist_transcript("v1", [seg(0.0, 5.0, "语音")], source_type="speech")
    persist_transcript("v1", [seg(0.0, 5.0, "画面")], source_type="keyframe")

    assert sorted(s["text"] for s in get_all_transcript_segments("v1")) == ["画面", "语音"]


def test_persist_transcript_rejects_rows_without_text(tmp_db, no_chroma):
    assert persist_transcript("v1", [{"start": 0.0, "end": 1.0}]) == 0


def test_window_upsert_matches_full_rebuild(tmp_db, no_chroma):


    incremental: list[dict] = []

    def fake_reindex(video_id, span_start, span_end, source_type="speech"):
        ws_lo = _window_start(span_start)
        upper = _window_start(span_end) + memory.INDEX_WINDOW_SECONDS
        with memory.get_db() as conn:
            rows = conn.execute(
                "SELECT start_time, end_time, text FROM transcript_segments"
                " WHERE video_id = ? AND source_type = ? AND start_time >= ? AND start_time < ?"
                " ORDER BY start_time",
                (video_id, source_type, ws_lo, upper),
            ).fetchall()

        segs = [{"start": r["start_time"], "end": r["end_time"], "text": r["text"]}
                for r in rows]
        incremental.extend(_build_index_units(video_id, segs, source_type))
        return len(segs)

    real = memory._reindex_windows
    memory._reindex_windows = fake_reindex
    try:
        persist_transcript("v1", [seg(0.0, 40.0, "甲")])
        persist_transcript("v1", [seg(50.0, 90.0, "乙")])
    finally:
        memory._reindex_windows = real

    full = {u["id"]: u for u in
            _build_index_units("v1", get_all_transcript_segments("v1"), "speech")}
    latest = {u["id"]: u for u in incremental}
    assert latest == full


def test_normalize_accepts_sqlite_row_keys():

    out = memory._normalize_segments([
        {"start_time": 100.0, "end_time": 105.0, "text": "甲"},
        {"start_time": 200.0, "end_time": 205.0, "text": "乙"},
    ])
    assert [s["start"] for s in out] == [100.0, 200.0]
    assert [s["end"] for s in out] == [105.0, 205.0]
    assert len(_build_index_units("v1", out, "speech")) == 2


def test_normalize_drops_blank_text():
    assert memory._normalize_segments([seg(0.0, 1.0, "   ")]) == []


def test_resolve_relative_path_against_project_root(tmp_path, monkeypatch):

    from backend.memory import resolve_media_path
    monkeypatch.chdir(tmp_path)
    resolved = resolve_media_path("data/audio/x.m4a")
    assert resolved == os.path.normpath(os.path.join(memory._PROJECT_ROOT, "data/audio/x.m4a"))
    assert os.path.isabs(resolved)


def test_resolve_absolute_path_is_unchanged(tmp_path):
    from backend.memory import resolve_media_path
    p = str(tmp_path / "v.mp4")
    assert resolve_media_path(p) == p


def test_resolve_empty_is_none():
    from backend.memory import resolve_media_path
    assert resolve_media_path("") is None
    assert resolve_media_path(None) is None


def test_media_file_exists(tmp_path):
    from backend.memory import media_file_exists
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x")
    assert media_file_exists(str(p))
    assert not media_file_exists(str(tmp_path / "nope.mp4"))
    assert not media_file_exists(None)
