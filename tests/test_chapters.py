
import pytest

from backend.chapters import (
    MAX_CHAPTERS,
    MIN_CHAPTER_SECONDS,
    _extract_json,
    _postprocess,
    chunk_transcript,
)


def seg(start, end, text="内容"):
    return {"start": start, "end": end, "text": text}


def chap(title, start, summary=""):
    return {"title": title, "start_time": start, "summary": summary}


def test_chunk_transcript_splits_at_block_seconds():
    segs = [seg(0, 10, "甲"), seg(10, 20, "乙"), seg(20, 30, "丙")]
    blocks = chunk_transcript(segs, block_seconds=20)
    assert len(blocks) == 2
    assert blocks[0] == {"start": 0.0, "text": "甲\n乙"}
    assert blocks[1] == {"start": 20.0, "text": "丙"}


def test_chunk_transcript_keeps_trailing_partial_block():
    blocks = chunk_transcript([seg(0, 5, "甲")], block_seconds=240)
    assert blocks == [{"start": 0.0, "text": "甲"}]


def test_chunk_transcript_empty():
    assert chunk_transcript([]) == []


def test_chunk_transcript_single_long_segment_is_its_own_block():
    blocks = chunk_transcript([seg(0, 300, "很长")], block_seconds=240)
    assert len(blocks) == 1


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_from_prose_and_code_fence():
    raw = '好的，结果如下：\n```json\n{"chapters": [{"title": "甲"}]}\n```\n以上。'
    assert _extract_json(raw) == {"chapters": [{"title": "甲"}]}


def test_extract_json_raises_on_garbage():
    with pytest.raises(RuntimeError):
        _extract_json("完全不是 JSON")


def test_extract_json_raises_on_empty():
    with pytest.raises(RuntimeError):
        _extract_json("")


def test_postprocess_fills_last_end_with_total_duration():
    out = _postprocess([chap("甲", 0), chap("乙", 100)], total_duration=300)
    assert [c["end_time"] for c in out] == [100.0, 300.0]


def test_postprocess_enforces_strictly_increasing_starts():

    out = _postprocess([chap("甲", 50), chap("乙", 50), chap("丙", 10)],
                       total_duration=300)
    starts = [c["start_time"] for c in out]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)


def test_postprocess_caps_chapter_count():
    many = [chap(f"第{i}章", i * 70) for i in range(MAX_CHAPTERS + 5)]
    out = _postprocess(many, total_duration=10_000)
    assert len(out) == MAX_CHAPTERS


def test_postprocess_merges_leading_fragment():

    out = _postprocess([chap("开场", 0), chap("正文", 8), chap("结尾", 200)],
                       total_duration=300)
    assert [c["title"] for c in out] == ["正文", "结尾"]
    assert out[0]["start_time"] == 0.0


def test_postprocess_keeps_short_first_chapter_when_it_is_the_only_one():
    out = _postprocess([chap("唯一一节", 0)], total_duration=30)
    assert len(out) == 1
    assert out[0]["start_time"] == 0.0


def test_postprocess_drops_blank_titles():
    out = _postprocess([chap("  ", 0), chap("正文", 10)], total_duration=300)
    assert [c["title"] for c in out] == ["正文"]


def test_postprocess_empty_input():
    assert _postprocess([], total_duration=300) == []
    assert _postprocess([chap("  ", 0)], total_duration=300) == []


def test_postprocess_orders_by_start_not_input_order():
    out = _postprocess([chap("后", 200), chap("前", 100)], total_duration=400)
    assert [c["title"] for c in out] == ["前", "后"]


def test_postprocess_clamps_end_to_total_duration():
    out = _postprocess([chap("甲", 0), chap("乙", 100)], total_duration=150)
    assert all(c["end_time"] <= 150 for c in out)


def test_postprocess_min_chapter_seconds_is_the_merge_threshold():
    out = _postprocess([chap("短", 0), chap("长", MIN_CHAPTER_SECONDS - 1)],
                       total_duration=300)
    assert [c["title"] for c in out] == ["长"]
