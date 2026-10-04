


import pytest

from backend.intent import classify, classify_with_reason


@pytest.mark.parametrize("question", ["", "   ", None])
def test_empty_question_falls_back_to_local(question):
    shape, why = classify_with_reason(question)
    assert shape == "local"
    assert why == "空问题"


@pytest.mark.parametrize("question", [
    "这一讲主要讲了什么",
    "这节课的核心是什么",
    "整节课的脉络",
    "整个视频在讲什么",
])
def test_range_cues_are_topic(question):
    assert classify(question) == "topic"


@pytest.mark.parametrize("question", [
    "这里老师说的 X 是什么意思",
    "这一段在干什么",
    "这附近提到了什么",
])
def test_local_cues_are_local(question):
    assert classify(question) == "local"


@pytest.mark.parametrize("question", [
    "这节课一共讲了哪几个知识点",
    "本章的结构是怎样的",
    "帮我梳理一下大纲",
    "老师是按什么顺序讲的",
])
def test_structure_cues_are_topic(question):
    assert classify(question) == "topic"


def test_range_cue_beats_local_cue():


    shape, why = classify_with_reason("这一讲里，这里说的定义是什么")
    assert shape == "topic"
    assert "范围指代" in why


def test_transition_question_is_topic():
    shape, why = classify_with_reason("从线性代数到概率论的过渡是怎么发生的")
    assert shape == "topic"
    assert "过渡" in why


def test_enumerate_question_is_topic():
    assert classify("整个视频讲了哪些例子") == "topic"


def test_plain_content_question_defaults_to_local():
    assert classify("这个公式里的符号代表什么") == "local"
    assert classify("为什么这里要加一个负号") == "local"


def test_reason_is_non_empty_for_every_shape():
    for q in ["这一讲讲了什么", "这里是什么", "一共讲了几个", "随便问点什么"]:
        shape, why = classify_with_reason(q)
        assert shape in ("topic", "local")
        assert why


def test_classify_matches_classify_with_reason():
    q = "这一讲的整体结构"
    assert classify(q) == classify_with_reason(q)[0]
