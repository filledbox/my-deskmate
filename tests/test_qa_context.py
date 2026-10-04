


import pytest

from backend.qa import (
    _cjk_bigrams,
    _coalesce,
    _fit_budget,
    _rec_key,
    format_chapter_catalog,
    format_context,
    merge_deduplicate,
)


def rec(rid, start, text, end=None, source_type="speech"):
    return {"id": rid, "source_type": source_type, "start_time": start,
            "end_time": start + 2 if end is None else end, "text": text}


def test_merge_deduplicate_dedupes_by_rec_key():
    a = rec("s1", 10.0, "第一个")

    dupe = rec("s1", 10.0, "重复但文本不同")
    out = merge_deduplicate([a], [dupe])
    assert len(out) == 1
    assert out[0]["text"] == "第一个"


def test_merge_deduplicate_first_wins():

    anchor = rec("s1", 10.0, "锚区版本")
    semantic = [rec("s1", 10.0, "语义版本"), rec("s2", 30.0, "语义独有")]
    out = merge_deduplicate([anchor], semantic)
    assert [r["text"] for r in out] == ["锚区版本", "语义独有"]


def test_merge_deduplicate_sorts_by_time():
    out = merge_deduplicate([rec("b", 90.0, "后")], [rec("a", 10.0, "前")])
    assert [r["start_time"] for r in out] == [10.0, 90.0]


def test_rec_key_distinguishes_source_type_and_start():
    assert _rec_key(rec("x", 1.0, "t", source_type="speech")) != \
           _rec_key(rec("x", 1.0, "t", source_type="scene"))
    assert _rec_key(rec("x", 1.0, "t")) != _rec_key(rec("x", 2.0, "t"))


def test_fit_budget_keeps_near_records_and_drops_far_ones():
    records = [rec(f"r{i}", 100.0 + i, "字" * 10) for i in (-5, -1, 0, 1, 5)]
    out = _fit_budget(records, position=100.0, budget=25)

    assert {r["id"] for r in out} == {"r-1", "r0"}
    assert [r["start_time"] for r in out] == sorted(r["start_time"] for r in out)


def test_fit_budget_anchor_survives_starvation():

    pos = 100.0
    anchor = [rec(f"a{i}", pos + i, "锚" * 20) for i in (-2, 0, 2)]
    far = [rec(f"f{i}", 900.0, "远" * 200) for i in range(5)]
    out = _fit_budget(anchor + far, pos, budget=1, anchor=anchor)
    assert {r["id"] for r in out} == {"a-2", "a0", "a2"}


def test_fit_budget_never_returns_empty_even_when_over_budget():

    out = _fit_budget([rec("big", 50.0, "长" * 5000)], position=50.0, budget=10)
    assert [r["id"] for r in out] == ["big"]


def test_coalesce_merges_within_block_window():
    segs = [rec("s1", 0.0, "甲", end=1.0), rec("s2", 5.0, "乙", end=6.0),
            rec("s3", 40.0, "丙", end=41.0)]
    blocks = _coalesce(segs, block_seconds=30.0)
    assert len(blocks) == 2
    assert blocks[0]["text"] == "甲乙"
    assert blocks[0]["end_time"] == 6.0
    assert blocks[1]["text"] == "丙"


def test_coalesce_ids_are_derived_from_time():

    blocks = _coalesce([rec("s1", 12.34, "甲", end=13.0)], block_seconds=30.0)
    assert blocks[0]["id"] == "blk123"


def test_coalesce_of_empty_is_empty():
    assert _coalesce([]) == []


def test_cjk_bigrams_ignores_non_cjk():
    assert _cjk_bigrams("线性代数") == {"线性", "性代", "代数"}
    assert _cjk_bigrams("abc线性 123") == {"线性"}
    assert _cjk_bigrams("") == set()
    assert _cjk_bigrams("单") == set()


def test_format_context_marks_playback_position():


    records = [rec("s1", 10.0, "上一句"), rec("s2", 70.0, "下一句")]
    text = format_context(records, position=50.0)
    lines = text.splitlines()
    assert lines[0] == "[SPEECH @ 0:10] 上一句"
    assert lines[1] == "—— 当前播放位置 0:50 ——"
    assert lines[2] == "[SPEECH @ 1:10] 下一句"


def test_format_context_appends_marker_when_position_is_past_the_end():
    text = format_context([rec("s1", 10.0, "只有一段")], position=99.0)
    assert text.splitlines()[-1] == "—— 当前播放位置 1:39 ——"


def test_format_context_without_position_has_no_marker():
    text = format_context([rec("s1", 10.0, "无位置")])
    assert "当前播放位置" not in text


def test_format_context_labels_scene_records():
    text = format_context([rec("k1", 3.0, "板书", source_type="keyframe")])
    assert text.startswith("[SCENE @ 0:03]")


def test_format_context_empty_returns_placeholder():
    assert "No preprocessed content" in format_context([])


def test_format_chapter_catalog_lists_titles_and_ranges():
    chapters = [
        {"title": "第一章", "start_time": 0, "end_time": 600, "summary": "绪论"},
        {"title": "第二章", "start_time": 600, "end_time": 1200, "summary": ""},
    ]
    text = format_chapter_catalog(chapters)
    assert text.splitlines()[0] == "全部章节："
    assert "0:00–10:00 第一章：绪论" in text
    assert text.splitlines()[2].endswith("第二章")


def test_format_chapter_catalog_empty_is_empty_string():
    assert format_chapter_catalog([]) == ""
