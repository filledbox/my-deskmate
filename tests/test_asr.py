


import pytest

from backend import asr


def test_runtime_metrics_reset_returns_an_independent_snapshot():
    asr._RUNTIME_METRICS["upload_attempts"] = 7
    asr._DEGRADED_FLAG["value"] = True
    asr.reset_runtime_metrics()
    snapshot = asr.runtime_metrics()
    assert snapshot == {
        "upload_attempts": 0,
        "upload_successes": 0,
        "upload_bytes": 0,
        "retry_count": 0,
        "degraded_windows": 0,
    }
    assert asr.degraded_in_this_process() is False
    snapshot["upload_attempts"] = 99
    assert asr.runtime_metrics()["upload_attempts"] == 0


def test_backfill_adds_offset():
    segs = [{"start": 0.5, "end": 3.25, "text": "你好"}]
    out = asr._backfill(segs, 300.0, 630.0)
    assert out == [{"start": 300.5, "end": 303.25, "text": "你好"}]


def test_backfill_rounds_once_after_adding_offset():

    out = asr._backfill([{"start": 0.005, "end": 0.015, "text": "x"}], 10.0, 20.0)
    assert out[0]["start"] == round(10.005, 2)
    assert out[0]["end"] == round(10.015, 2)


def test_backfill_clamps_hallucinated_timestamps_into_the_clip():

    segs = [
        {"start": -5.0, "end": 2.0, "text": "前"},
        {"start": 250.0, "end": 999.0, "text": "后"},
    ]
    out = asr._backfill(segs, 300.0, 630.0)
    assert out[0]["start"] == 300.0
    assert out[1]["end"] == 630.0


def test_backfill_sorts_by_start():
    segs = [
        {"start": 10.0, "end": 12.0, "text": "b"},
        {"start": 1.0, "end": 2.0, "text": "a"},
    ]
    assert [s["text"] for s in asr._backfill(segs, 0.0, 100.0)] == ["a", "b"]


def test_backfill_drops_empty_text_and_zero_length():
    segs = [
        {"start": 1.0, "end": 2.0, "text": "   "},
        {"start": 3.0, "end": 3.0, "text": "零长"},
        {"start": 4.0, "end": 5.0, "text": "留下"},
    ]
    assert [s["text"] for s in asr._backfill(segs, 0.0, 100.0)] == ["留下"]


def test_backfill_accepts_object_segments_like_the_openai_sdk_returns():
    class Seg:
        start, end, text = 1.0, 2.0, "对象"

    assert asr._backfill([Seg()], 0.0, 100.0)[0]["text"] == "对象"


def test_segments_usable_rejects_all_zero_starts():

    segs = [{"start": 0.0, "end": 5.0, "text": "a"},
            {"start": 0.0, "end": 5.0, "text": "b"}]
    assert asr._segments_usable(segs) is False


def test_segments_usable_rejects_non_monotonic():
    segs = [{"start": 5.0, "end": 8.0, "text": "a"},
            {"start": 1.0, "end": 3.0, "text": "b"}]
    assert asr._segments_usable(segs) is False


def test_segments_usable_rejects_single_segment():

    assert asr._segments_usable([{"start": 0.0, "end": 9.0, "text": "a"}]) is False


def test_segments_usable_accepts_normal_output():
    segs = [{"start": 0.0, "end": 3.0, "text": "a"},
            {"start": 3.0, "end": 7.5, "text": "b"}]
    assert asr._segments_usable(segs) is True


def test_aligned_bounds_tile_without_gap_or_overlap():
    bounds = asr._aligned_bounds(280.0, 610.0)
    assert bounds[0][0] == 280.0
    assert bounds[-1][1] == 610.0
    for (_, prev_end), (next_start, _) in zip(bounds, bounds[1:]):
        assert prev_end == next_start
    assert bounds == [(280.0, 300.0), (300.0, 360.0), (360.0, 420.0),
                      (420.0, 480.0), (480.0, 540.0), (540.0, 600.0),
                      (600.0, 610.0)]


def test_aligned_bounds_on_exact_grid_start_has_no_zero_length_first():
    bounds = asr._aligned_bounds(300.0, 400.0)
    assert bounds[0] == (300.0, 360.0)
    assert all(e > s for s, e in bounds)


def test_split_conserves_every_character():
    text = "".join(f"第{i}段内容，这里是一些用来测试的文本。" for i in range(80))
    segs = asr._split_text_over_windows(text, 0.0, 330.0)
    assert "".join(s["text"] for s in segs) == text


def test_split_never_emits_a_segment_longer_than_the_grid():
    text = "".join(f"第{i}段内容，这里是一些用来测试的文本。" for i in range(80))
    for s in asr._split_text_over_windows(text, 280.0, 610.0):
        assert s["end"] - s["start"] <= asr._FALLBACK_GRID_SECONDS + 1e-6


def test_split_keeps_every_position_reachable_by_the_pm30_search():


    text = "".join(f"第{i}段内容，这里是一些用来测试的文本。" for i in range(120))
    segs = asr._split_text_over_windows(text, 280.0, 610.0)
    starts = [s["start"] for s in segs]
    p = 280.0
    while p <= 610.0:
        assert any(p - 30 <= st <= p + 30 for st in starts), f"{p}s 处没有任何段可命中"
        p += 5.0


def test_split_snaps_cuts_to_punctuation():
    text = "第一句内容，第二句内容，第三句内容，第四句内容，第五句内容，第六句内容。"
    segs = asr._split_text_over_windows(text, 0.0, 120.0)

    for s in segs[:-1]:
        assert s["text"][-1] in asr._PUNCT


def test_split_empty_text_yields_nothing():
    assert asr._split_text_over_windows("   ", 0.0, 60.0) == []
    assert asr._split_text_over_windows("x", 10.0, 10.0) == []


def test_enforce_max_segment_splits_only_the_long_ones():
    segs = [
        {"start": 0.0, "end": 5.0, "text": "短"},
        {"start": 100.0, "end": 300.0, "text": "长" * 400},
    ]
    out = asr._enforce_max_segment(segs)
    assert out[0] == segs[0]
    assert all(s["end"] - s["start"] <= asr._FALLBACK_GRID_SECONDS + 1e-6
               for s in out[1:])
    assert "".join(s["text"] for s in out[1:]) == "长" * 400


def test_supersede_drops_rows_overlapping_the_new_span():


    kept = [
        {"start": 271.5, "end": 277.0, "text": "留：end 没越过新起点"},
        {"start": 277.0, "end": 281.4, "text": "删：跨在新起点上"},
        {"start": 296.0, "end": 299.6, "text": "删：落在新覆盖区内"},
        {"start": 605.0, "end": 610.0, "text": "留：起点越过新覆盖区尾部"},
    ]
    new = [{"start": 280.2, "end": 599.9, "text": "新"}]
    out = asr._supersede(kept, new)
    assert [s["text"] for s in out] == [
        "留：end 没越过新起点", "留：起点越过新覆盖区尾部", "新",
    ]


def test_supersede_agrees_with_the_real_sql_predicate(tmp_db):


    from backend import memory

    kept = [
        {"start": s, "end": s + 4.0, "text": f"r{s}"}
        for s in (0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0)
    ]
    new = [{"start": 12.0, "end": 22.0, "text": "新"}]

    with memory.get_db() as conn:
        conn.executemany(
            "INSERT INTO transcript_segments"
            " (video_id, start_time, end_time, text, source_type)"
            " VALUES (?, ?, ?, ?, 'speech')",
            [("v1", s["start"], s["end"], s["text"]) for s in kept],
        )
        conn.commit()

    deleted = memory._replace_rows_in_span("v1", 12.0, 22.0)
    with memory.get_db() as conn:
        rows = conn.execute(
            "SELECT text FROM transcript_segments WHERE video_id = 'v1'"
            " ORDER BY start_time"
        ).fetchall()

    sql_survivors = [r["text"] for r in rows]
    mem_survivors = [s["text"] for s in asr._supersede(kept, new) if s["text"] != "新"]

    assert sql_survivors == mem_survivors
    assert deleted == len(kept) - len(sql_survivors)


    assert "r5.0" in sql_survivors
    assert "r10.0" not in sql_survivors and "r20.0" not in sql_survivors
    assert "r25.0" in sql_survivors
    assert deleted == 3


def test_supersede_with_empty_new_keeps_everything():
    kept = [{"start": 0.0, "end": 1.0, "text": "a"}]
    assert asr._supersede(kept, []) == kept


def test_supersede_is_idempotent_on_repeated_identical_windows():

    new = [{"start": 280.0, "end": 290.0, "text": "x"}]
    once = asr._supersede([], new)
    assert asr._supersede(once, new) == once


@pytest.fixture
def asr_env(monkeypatch):
    monkeypatch.setenv("ASR_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("ASR_API_KEY", "sk-test")
    monkeypatch.delenv("ASR_MODEL", raising=False)
    monkeypatch.delenv("ASR_SLICE_FORMAT", raising=False)
    monkeypatch.delenv("ASR_MAX_UPLOAD_MB", raising=False)
    monkeypatch.delenv("ASR_LANGUAGE", raising=False)
    return monkeypatch


def test_resolve_requires_key(asr_env):
    asr_env.delenv("ASR_API_KEY")
    with pytest.raises(asr.ASRConfigError, match="ASR_API_KEY"):
        asr.resolve()


def test_resolve_requires_base_url(asr_env):

    asr_env.delenv("ASR_BASE_URL")
    with pytest.raises(asr.ASRConfigError, match="ASR_BASE_URL"):
        asr.resolve()


def test_resolve_defaults_to_chinese_and_verbose_json(asr_env):
    cfg = asr.resolve()
    assert cfg.language == "zh"
    assert cfg.response_format == "verbose_json"
    assert cfg.model == asr.DEFAULT_MODEL


def test_resolve_empty_language_means_autodetect(asr_env):
    asr_env.setenv("ASR_LANGUAGE", "")
    assert asr.resolve().language is None


def test_resolve_rejects_unknown_slice_format(asr_env):
    asr_env.setenv("ASR_SLICE_FORMAT", "aac")
    with pytest.raises(asr.ASRConfigError, match="ASR_SLICE_FORMAT"):
        asr.resolve()


def test_resolve_rejects_a_window_that_would_blow_the_upload_cap(asr_env, monkeypatch):

    monkeypatch.setattr(asr, "TRANSCRIBE_WINDOW_SECONDS", 900.0)
    with pytest.raises(asr.ASRConfigError, match="切片太大"):
        asr.resolve()


def test_resolve_size_guard_uses_window_plus_overlap(asr_env, monkeypatch):


    monkeypatch.setattr(asr, "TRANSCRIBE_WINDOW_SECONDS", 770.0)
    monkeypatch.setattr(asr, "TRANSCRIBE_OVERLAP_SECONDS", 30.0)
    asr_env.setenv("ASR_MAX_UPLOAD_MB", "25")
    assert 770 * 32_000 <= 25_000_000
    with pytest.raises(asr.ASRConfigError, match="切片太大"):
        asr.resolve()


def test_resolve_accepts_the_defaults(asr_env, monkeypatch):

    assert asr.resolve().slice_format == "wav"
