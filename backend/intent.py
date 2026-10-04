


import re


_RANGE_CUES = (
    "这一讲", "这一节", "这节课", "这一课", "这一小节", "这一章", "这一部分",
    "整讲", "整节", "整节课", "全片", "整个视频", "这个视频", "本节课", "这门课",
    "这段视频", "全程", "通篇",
)


_LOCAL_CUES = (
    "这里", "这一段", "这附近", "这一句", "这会儿", "现在", "刚才", "此刻",
)
_STRUCTURE_CUES = (
    "一共讲了", "讲了哪", "讲了什么", "哪些知识点", "哪几个", "几步", "分几步",
    "顺序", "脉络", "结构", "框架", "大纲", "目录", "总结", "梳理", "归纳",
    "分为几", "几部分", "几个方面", "先讲", "然后讲", "最后讲", "依次",
)


_TRANSITION_RE = re.compile(r"从.{1,24}到.{1,24}")
_TRANSITION_CUES = ("过渡", "衔接", "演变", "推导", "老师讲", "讲解", "讲了")


_ENUMERATE_RE = re.compile(r"(一共|总共|整个|全部|通篇).{0,8}(哪[几两]|几[种类个步])")


def classify_with_reason(question: str) -> tuple[str, str]:


    q = (question or "").strip()
    if not q:
        return "local", "空问题"
    for cue in _RANGE_CUES:
        if cue in q:
            return "topic", f"范围指代「{cue}」"
    for cue in _LOCAL_CUES:
        if cue in q:
            return "local", f"局部指代「{cue}」"
    for cue in _STRUCTURE_CUES:
        if cue in q:
            return "topic", f"结构问法「{cue}」"
    if _TRANSITION_RE.search(q) and any(c in q for c in _TRANSITION_CUES):
        return "topic", "跨段过渡问法"
    if _ENUMERATE_RE.search(q):
        return "topic", "整段枚举问法"
    return "local", "无结构线索"


def classify(question: str) -> str:
    return classify_with_reason(question)[0]
