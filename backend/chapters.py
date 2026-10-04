


import asyncio
import json
import re

from openai import AsyncOpenAI, BadRequestError, UnprocessableEntityError

from .llm import ROLE_CHAPTER, chat_with_retry, make_client, model_for


BLOCK_SECONDS = 240.0
MAX_CHAPTERS = 12

MIN_CHAPTER_SECONDS = 60.0
_MAX_SUMMARY_CHARS = 1500


_SUMMARY_MAX_TOKENS = 1024

_GROUP_MAX_TOKENS = 4096


def chunk_transcript(segments: list[dict], block_seconds: float = BLOCK_SECONDS) -> list[dict]:

    blocks: list[dict] = []
    cur_start: float | None = None
    cur_parts: list[str] = []
    for seg in segments:
        if cur_start is None:
            cur_start = float(seg["start"])
        cur_parts.append(str(seg["text"]))
        cur_end = float(seg["end"])
        if cur_end - cur_start >= block_seconds:
            blocks.append({"start": cur_start, "text": "\n".join(cur_parts)})
            cur_start = None
            cur_parts = []
    if cur_parts and cur_start is not None:
        blocks.append({"start": cur_start, "text": "\n".join(cur_parts)})
    return blocks


async def _summarize_one(client: AsyncOpenAI, block: dict) -> str:
    resp = await chat_with_retry(
        client,
        model=model_for(ROLE_CHAPTER),
        max_tokens=_SUMMARY_MAX_TOKENS,
        messages=[{
            "role": "user",
            "content": (
                "这是一段网课视频的语音转写。用一句话（不超过 30 字）概括这段在讲什么，"
                "直接给出概括，不要加引号、冒号或任何前缀。\n\n转写：\n"
                + block["text"][:_MAX_SUMMARY_CHARS]
            ),
        }],
    )
    return (resp.choices[0].message.content or "").strip()


async def summarize_blocks(blocks: list[dict], client: AsyncOpenAI, concurrency: int = 1) -> list[dict]:

    sem = asyncio.Semaphore(concurrency)

    async def one(block: dict) -> dict:
        async with sem:
            return {"start": block["start"], "summary": await _summarize_one(client, block)}

    return await asyncio.gather(*(one(b) for b in blocks))


async def propose_chapters(block_summaries: list[dict], client: AsyncOpenAI,
                           total_duration: float) -> list[dict]:

    listing = "\n".join(
        f"{i}. [{b['start']:.0f}s] {b['summary']}" for i, b in enumerate(block_summaries)
    )
    prompt = f"""下面是一个网课视频按时间切块的摘要列表，每行格式：序号. [开始秒] 摘要。

{listing}

请把这些块合并成 3~{MAX_CHAPTERS} 个主题章节，每章内部主题一致。输出 JSON，不要任何其他文字，格式：
{{"chapters": [{{"title": "章节标题", "start_time": 起始秒, "end_time": 结束秒, "summary": "一句话章节摘要"}}]}}

要求：
- 章节按时间先后排列
- start_time 取该章第一块的开始秒，end_time 取该章最后一块的结束秒
- 严格使用上面给定的块开始秒，不要凭空造时间
- 标题 ≤ 12 字，summary 一句话
- 章节数 3~{MAX_CHAPTERS}"""
    try:
        resp = await chat_with_retry(
            client,
            model=model_for(ROLE_CHAPTER),
            response_format={"type": "json_object"},
            max_tokens=_GROUP_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except (BadRequestError, UnprocessableEntityError):


        resp = await chat_with_retry(
            client,
            model=model_for(ROLE_CHAPTER),
            max_tokens=_GROUP_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    return _parse_chapters_json(resp.choices[0].message.content or "", total_duration)


def _parse_chapters_json(raw: str, total_duration: float) -> list[dict]:
    data = _extract_json(raw)
    chapters = data.get("chapters", [])
    out: list[dict] = []
    for c in chapters:
        try:
            start = max(0.0, float(c.get("start_time", 0)))
            end = float(c.get("end_time", 0) or total_duration)
        except (TypeError, ValueError):
            continue
        out.append({
            "title": str(c.get("title", "")).strip(),
            "start_time": start,
            "end_time": end,
            "summary": str(c.get("summary", "")).strip(),
        })
    return out


def _extract_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise RuntimeError(f"LLM 返回无法解析的 JSON: {raw[:200]}")


def _postprocess(chapters: list[dict], total_duration: float) -> list[dict]:

    if not chapters:
        return []
    chapters = [c for c in chapters if c["title"].strip()]
    if not chapters:
        return []
    chapters.sort(key=lambda c: c["start_time"])
    if len(chapters) > MAX_CHAPTERS:
        chapters = chapters[:MAX_CHAPTERS]

    starts = [c["start_time"] for c in chapters]
    for i in range(1, len(starts)):
        if starts[i] <= starts[i - 1]:
            starts[i] = starts[i - 1] + 1.0

    out: list[dict] = []
    for i, c in enumerate(chapters):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(starts) else total_duration
        if end <= start:
            end = total_duration
        out.append({
            "title": c["title"].strip(),
            "start_time": round(start, 1),
            "end_time": round(min(end, total_duration), 1),
            "summary": c.get("summary", "").strip(),
        })


    while len(out) > 1 and out[0]["end_time"] - out[0]["start_time"] < MIN_CHAPTER_SECONDS:
        out[1]["start_time"] = out[0]["start_time"]
        out.pop(0)
    return out


async def segment_into_chapters(segments: list[dict], total_duration: float | None = None) -> list[dict]:

    if not segments:
        return []
    if total_duration is None:
        total_duration = float(segments[-1]["end"])
    client = make_client(ROLE_CHAPTER)
    blocks = chunk_transcript(segments)
    summaries = await summarize_blocks(blocks, client)
    chapters = await propose_chapters(summaries, client, total_duration)
    return _postprocess(chapters, total_duration)
