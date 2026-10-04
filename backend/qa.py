import os
import json
from . import intent
from .memory import (
    search_by_time_window,
    search_semantic,
    search_transcript_window,
    insert_chat_message,
    get_recent_chat_messages,
    get_keyframes_near,
    get_video,
    get_chapters,
    get_chapters_at,
    get_transcription_coverage,
)
from .llm import ROLE_QA, chat_with_retry, make_client, model_for
from .preprocess import prepare_frame_for_api


_QA_MAX_TOKENS = 2048


_HISTORY_CONTENT_MAX_CHARS = 600


_CONTEXT_MAX_CHARS = 20_000


_CHAPTER_PAD_SECONDS = 20.0


_CHAPTER_FALLBACK_TOP_N = 3


_FULL_TRANSCRIPT_LIMIT = 100_000


_TOPIC_BLOCK_SECONDS = 30.0


def _format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _recent_history(video_id: str, conversation_id: str) -> list[dict]:


    try:
        limit = int(os.getenv("QA_HISTORY_MESSAGES", "6"))
    except ValueError:
        limit = 6
    if limit <= 0:
        return []
    history: list[dict] = []
    for m in get_recent_chat_messages(video_id, limit=limit, conversation_id=conversation_id):
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if len(content) > _HISTORY_CONTENT_MAX_CHARS:
            content = content[:_HISTORY_CONTENT_MAX_CHARS] + "…"
        history.append({"role": m["role"], "content": content})
    return history


def _rec_key(r: dict) -> str:


    return f"{r.get('id', '')}_{r.get('source_type', '')}_{r.get('start_time', 0)}"


def merge_deduplicate(
    time_records: list[dict],
    semantic_records: list[dict],
) -> list[dict]:


    seen = set()
    merged = []
    for r in time_records + semantic_records:
        key = _rec_key(r)
        if key not in seen:
            seen.add(key)
            merged.append(r)
    merged.sort(key=lambda r: r.get("start_time", 0))
    return merged


def _fit_budget(records: list[dict], position: float, budget: int,
                *, anchor: list[dict] | None = None) -> list[dict]:


    anchor_keys = {_rec_key(r) for r in (anchor or [])}
    kept = [r for r in records if _rec_key(r) in anchor_keys]
    used = sum(len(r.get("text") or "") for r in kept)
    rest = sorted(
        (r for r in records if _rec_key(r) not in anchor_keys),
        key=lambda r: (abs(r.get("start_time", 0) - position), r.get("start_time", 0)),
    )
    for r in rest:
        cost = len(r.get("text") or "")
        if used + cost > budget and kept:
            break
        kept.append(r)
        used += cost
    kept.sort(key=lambda r: r.get("start_time", 0))
    return kept


def _coalesce(records: list[dict], block_seconds: float = _TOPIC_BLOCK_SECONDS) -> list[dict]:


    ordered = sorted((r for r in records), key=lambda r: r.get("start_time", 0))
    blocks: list[dict] = []
    cur: dict | None = None
    for r in ordered:
        ts = r.get("start_time", 0)
        if cur is None or ts - cur["start_time"] >= block_seconds:
            if cur is not None:
                blocks.append(cur)
            cur = {
                "id": f"blk{int(ts * 10)}", "source_type": "speech",
                "start_time": ts, "end_time": r.get("end_time", ts),
                "text": r.get("text", ""),
            }
        else:
            cur["text"] += r.get("text", "")
            cur["end_time"] = r.get("end_time", cur["end_time"])
    if cur is not None:
        blocks.append(cur)
    return blocks


def _cjk_bigrams(text: str) -> set[str]:
    chars = [c for c in (text or "") if 0x4E00 <= ord(c) <= 0x9FFF]
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


def format_context(records: list[dict], position: float | None = None) -> str:


    if not records:
        return "(No preprocessed content available for this section of the video yet.)"
    lines = []
    marked = position is None
    for r in records:
        ts = r.get("start_time", 0)
        if not marked and ts > position:
            lines.append(f"—— 当前播放位置 {_format_time(position)} ——")
            marked = True
        source = "SPEECH" if r.get("source_type") == "speech" else "SCENE"
        lines.append(f"[{source} @ {_format_time(ts)}] {r.get('text', '')}")
    if not marked:
        lines.append(f"—— 当前播放位置 {_format_time(position)} ——")
    return "\n".join(lines)


def format_chapter_catalog(chapters: list[dict]) -> str:


    if not chapters:
        return ""
    lines = ["全部章节："]
    for c in chapters:
        line = f"  {_format_time(c['start_time'])}–{_format_time(c['end_time'])} {c['title']}"
        if c.get("summary"):
            line += f"：{c['summary']}"
        lines.append(line)
    return "\n".join(lines)


CITATION_RULE = (
    "\n\n回答规范：引用视频中的具体时刻时，用 [MM:SS] 格式标注（如 [43:05]），"
    "且只能标注本回答实际依据的、上文上下文中出现过的时刻。"
)


def format_chapter_context(chapters_at: list[dict], position: float) -> str:

    if not chapters_at:
        return ""
    current = chapters_at[-1]
    block = (
        f"当前章节：{current['title']}"
        f"（{_format_time(current['start_time'])}~{_format_time(current['end_time'])}）"
    )
    if current.get("summary"):
        block += f"。{current['summary']}"
    prev = chapters_at[:-1]
    if prev:
        prev_lines = "；".join(
            f"{c['title']}（{c.get('summary', '')}）" for c in prev
        )
        block += f"\n前情章节：{prev_lines}"
    return block


def retrieve_context(video_id: str, playback_position: float, question: str,
                     *, include_images: bool = True,
                     policy: str = "window") -> tuple[list[dict], list[str]]:


    policy = _resolve_policy(policy, question)
    if policy not in CONTEXT_POLICIES:
        raise ValueError(f"未知 policy {policy!r}，可用: ('auto',) + {CONTEXT_POLICIES}")
    records = _retrieve_records(
        video_id, playback_position, question, policy, _CONTEXT_MAX_CHARS
    )

    image_paths: list[str] = []
    if include_images:
        keyframes = get_keyframes_near(video_id, playback_position, max_frames=3)
        image_paths = [kf["image_path"] for kf in keyframes]

    return records, image_paths


CONTEXT_POLICIES: tuple[str, ...] = ("window", "chapter", "chapter_topic")

AUTO_POLICY = "auto"


def _resolve_policy(policy: str, question: str) -> str:


    if policy != AUTO_POLICY:
        return policy
    shape, _ = intent.classify_with_reason(question)
    return "chapter_topic" if shape == "topic" else "chapter"


def _containing_chapter(video_id: str, position: float) -> dict | None:

    at = get_chapters_at(video_id, position, lookback=0)
    return at[-1] if at else None


def _segments_of_chapters(video_id: str, chapters: list[dict]) -> list[dict]:

    out: list[dict] = []
    for c in chapters:
        out += search_transcript_window(
            video_id,
            max(0.0, c["start_time"] - _CHAPTER_PAD_SECONDS),
            c["end_time"] + _CHAPTER_PAD_SECONDS,
        )
    return out


def _chapters_by_overlap(video_id: str, question: str,
                         top_n: int = _CHAPTER_FALLBACK_TOP_N) -> list[dict]:


    chapters = get_chapters(video_id)
    if not chapters:
        return []
    q_grams = _cjk_bigrams(question)
    if not q_grams:
        return _segments_of_chapters(video_id, chapters[:top_n])
    ranked = sorted(
        chapters,
        key=lambda c: len(q_grams & _cjk_bigrams(f"{c['title']}{c.get('summary') or ''}"))
        / len(q_grams),
        reverse=True,
    )
    return _segments_of_chapters(video_id, ranked[:top_n])


def _retrieve_records(video_id: str, playback_position: float, question: str,
                      policy: str, budget: int) -> list[dict]:


    anchor = search_by_time_window(video_id, playback_position, window_seconds=30)

    if policy == "window":
        semantic_records = search_semantic(video_id, question, k=5)
        merged = merge_deduplicate(anchor, semantic_records)
        merged.sort(key=lambda r: abs(r.get("start_time", 0) - playback_position))
        return _fit_budget(merged, playback_position, budget)

    if policy == "chapter":
        chapter = _containing_chapter(video_id, playback_position)
        lo = hi = None
        if chapter:
            lo = max(0.0, chapter["start_time"] - _CHAPTER_PAD_SECONDS)
            hi = chapter["end_time"] + _CHAPTER_PAD_SECONDS
        chapter_records = search_transcript_window(video_id, lo, hi) if chapter else []


        semantic_records = [
            r for r in search_semantic(video_id, question, k=10)
            if lo is None or not (lo <= r.get("start_time", 0) <= hi)
        ]
        merged = merge_deduplicate(anchor, chapter_records + semantic_records)
        return _fit_budget(merged, playback_position, budget, anchor=anchor)

    if policy == "chapter_topic":
        whole = _coalesce(search_transcript_window(
            video_id, 0.0, 1e9, limit=_FULL_TRANSCRIPT_LIMIT
        ))
        whole_cost = sum(len(r.get("text") or "") for r in whole)
        if whole and whole_cost <= budget:


            return whole
        semantic_records = search_semantic(video_id, question, k=10)
        fallback = _chapters_by_overlap(video_id, question)
        merged = merge_deduplicate(anchor, semantic_records + fallback)
        return _fit_budget(merged, playback_position, budget, anchor=anchor)

    raise ValueError(f"policy {policy!r} 尚无实现（应已在 CONTEXT_POLICIES 里）")


def build_qa_prompt(video_id: str, playback_position: float, question: str,
                    *, include_chapters: bool = True,
                    include_images: bool = True,
                    policy: str = "window") -> tuple[str, list[str]]:


    merged_records, image_paths = retrieve_context(
        video_id, playback_position, question, include_images=include_images, policy=policy
    )
    chapter_block = ""
    if include_chapters:
        chapter_block = format_chapter_context(
            get_chapters_at(video_id, playback_position), playback_position
        )
        if _resolve_policy(policy, question) == "chapter_topic":

            catalog = format_chapter_catalog(get_chapters(video_id))
            if catalog:
                chapter_block = f"{catalog}\n\n{chapter_block}" if chapter_block else catalog

    if image_paths:


        speech_records = [r for r in merged_records if r.get("source_type") == "speech"]
        text_context = format_context(speech_records, playback_position)
        prompt = f"""You are watching a video. Below are the video frames you are seeing right now, plus the corresponding speech transcript near {_format_time(playback_position)}:

{text_context}

{chapter_block}

User question: {question}

Answer concisely based on what you see in the frames and hear in the transcript. If the provided information is insufficient, say so honestly."""
    else:
        text_context = format_context(merged_records, playback_position)
        prompt = f"""You are a helpful assistant answering questions about a video. Below is the relevant preprocessed content near {_format_time(playback_position)}:

{text_context}

{chapter_block}

User question: {question}

Answer concisely based on the provided content. If the context doesn't contain enough information, say so honestly."""

    return prompt + CITATION_RULE, image_paths


def build_qa_messages(video_id: str, playback_position: float, question: str,
                      *, include_chapters: bool = True,
                      include_images: bool = True,
                      history: list[dict] | None = None,
                      policy: str = "window") -> tuple[list[dict], list[str]]:


    prompt, image_paths = build_qa_prompt(
        video_id, playback_position, question,
        include_chapters=include_chapters, include_images=include_images,
        policy=policy,
    )
    messages: list[dict] = list(history or [])

    if image_paths:
        content_blocks: list[dict] = []
        for img_path in image_paths:
            try:
                b64_data = prepare_frame_for_api(img_path)
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"},
                })
            except Exception:
                pass
        content_blocks.append({"type": "text", "text": prompt})
        messages.append({"role": "user", "content": content_blocks})
    else:
        messages.append({"role": "user", "content": prompt})

    return messages, image_paths


async def stream_answer(
    video_id: str,
    playback_position: float,
    question: str,
    send_fn,
    *,
    conversation_id: str,
):


    video = get_video(video_id)
    ready = bool(video and video["status"] == "ready")
    coverage = get_transcription_coverage(video_id)

    if not ready and coverage < playback_position:
        await send_fn({
            "type": "qa_warning",
            "message": (
                f"Video preprocessed up to {_format_time(coverage)}. "
                f"Your question is about {_format_time(playback_position)}, "
                f"which may not be fully processed yet."
            ),
        })


    history = _recent_history(video_id, conversation_id)
    insert_chat_message(video_id, "user", question, playback_position,
                        conversation_id=conversation_id)


    messages, _ = build_qa_messages(
        video_id, playback_position, question, history=history, policy=AUTO_POLICY
    )

    answer_parts: list[str] = []
    client = make_client(ROLE_QA)

    stream = await chat_with_retry(
        client,
        model=model_for(ROLE_QA),
        max_tokens=_QA_MAX_TOKENS,
        messages=messages,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            answer_parts.append(delta)
            await send_fn({"type": "qa_chunk", "text": delta})

    full_answer = "".join(answer_parts)
    insert_chat_message(video_id, "assistant", full_answer, playback_position,
                        conversation_id=conversation_id)
    await send_fn({"type": "qa_done"})


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_transcript",
            "description": "Search speech/subtitle transcript within a specific time range. Returns text with timestamps. Use for questions about what was said at a particular moment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time": {"type": "number", "description": "Start time in seconds"},
                    "end_time": {"type": "number", "description": "End time in seconds"},
                    "limit": {"type": "integer", "description": "Max segments to return (default 10)"},
                },
                "required": ["start_time", "end_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_content",
            "description": "Semantically search speech transcript. Best for topic/keyword queries across the whole video.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query in natural language"},
                    "k": {"type": "integer", "description": "Number of results (default 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_keyframes",
            "description": "Get video frames (images) near a timestamp. Use when you need to see what's visually on screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "number", "description": "Timestamp in seconds"},
                    "max_frames": {"type": "integer", "description": "Max frames to return (default 3)"},
                },
                "required": ["timestamp"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_video_info",
            "description": "Get video metadata: title, duration, processing status. Use to check video length or whether preprocessing is complete.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _build_system_prompt(video_id: str, playback_position: float) -> str:
    video = get_video(video_id)
    title = video["title"] if video else "Unknown"
    duration = video["duration_seconds"] if video else 0
    processed = get_transcription_coverage(video_id)
    chapter_block = format_chapter_context(
        get_chapters_at(video_id, playback_position), playback_position
    )

    chapter_section = f"Chapter context:\n{chapter_block}\n\n" if chapter_block else ""

    return f"""You are a video Q&A assistant. The user is watching a video and asking questions.

Video: {title}
Duration: {_format_time(duration)}
User's playback position: {_format_time(playback_position)}
Preprocessed up to: {_format_time(processed)}

{chapter_section}You have tools to search the video. Use them proactively:
- search_transcript  — find speech in a time range
- search_content     — semantic search across all content
- get_keyframes      — get visual frames near a timestamp
- get_video_info     — check video metadata

Guidelines:
- For questions about a specific moment, search near that time.
- For general/topic questions, use semantic search.
- For visual questions ("what do you see"), request keyframes.
- One tool call is often enough. Gather context, then answer.
- Answer concisely. If context is insufficient, say so.
- When citing a specific moment in your answer, use [MM:SS] format (e.g. [43:05]), and only cite timestamps that appeared in the context/tool results you actually used."""


async def _execute_tools(tool_calls, video_id: str) -> list[dict]:
    results = []
    for tc in tool_calls:
        name = tc.function.name
        args = json.loads(tc.function.arguments) if tc.function.arguments else {}

        if name == "search_transcript":
            rows = search_transcript_window(
                video_id,
                args.get("start_time", 0),
                args.get("end_time", 0),
                args.get("limit", 10),
            )
            text = format_context(rows) if rows else "(No transcript in this range)"
            results.append({"tool_call_id": tc.id, "role": "tool", "content": text})

        elif name == "search_content":
            rows = search_semantic(video_id, args.get("query", ""), args.get("k", 5))
            text = format_context(rows) if rows else "(No matching content)"
            results.append({"tool_call_id": tc.id, "role": "tool", "content": text})

        elif name == "get_keyframes":
            kfs = get_keyframes_near(
                video_id,
                args.get("timestamp", 0),
                args.get("max_frames", 3),
            )
            lines = [f"Keyframes near {_format_time(args.get('timestamp', 0))}:"]
            for kf in kfs:
                lines.append(f"  {_format_time(kf['timestamp'])} — {kf.get('image_path', '')}")
            text = "\n".join(lines) if kfs else "(No keyframes in this range)"
            results.append({
                "tool_call_id": tc.id,
                "role": "tool",
                "content": text,
                "_image_paths": [kf["image_path"] for kf in kfs],
            })

        elif name == "get_video_info":
            video = get_video(video_id)
            parts = []
            if video:
                parts.append(f"Title: {video['title']}")
                parts.append(f"Duration: {_format_time(video.get('duration_seconds', 0))}")
                parts.append(f"Status: {video['status']}")
            parts.append(f"Preprocessed up to: {_format_time(get_transcription_coverage(video_id))}")
            text = "\n".join(parts) if parts else "Video not found"
            results.append({"tool_call_id": tc.id, "role": "tool", "content": text})

    return results


async def stream_answer_agentic(
    video_id: str,
    playback_position: float,
    question: str,
    send_fn,
    *,
    conversation_id: str,
):
    client = make_client(ROLE_QA)
    model = model_for(ROLE_QA)

    system_prompt = _build_system_prompt(video_id, playback_position)

    history = _recent_history(video_id, conversation_id)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": question},
    ]

    insert_chat_message(video_id, "user", question, playback_position,
                        conversation_id=conversation_id)

    for _ in range(5):
        response = await chat_with_retry(
            client,
            model=model,
            max_tokens=_QA_MAX_TOKENS,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        choice = response.choices[0]

        if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
            tool_calls = choice.message.tool_calls

            for tc in tool_calls:
                try:
                    tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    tool_args = {}
                await send_fn({
                    "type": "tool_call",
                    "tool": tc.function.name,
                    "args": tool_args,
                })

            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            })

            tool_results = await _execute_tools(tool_calls, video_id)

            collected_images: list[str] = []
            for tr in tool_results:
                collected_images.extend(tr.pop("_image_paths", []))

            messages.extend(tool_results)

            if collected_images:
                content_blocks: list[dict] = []
                for img_path in collected_images:
                    try:
                        b64 = prepare_frame_for_api(img_path)
                        content_blocks.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        })
                    except Exception:
                        pass
                content_blocks.append({
                    "type": "text",
                    "text": "These are the keyframes you requested. Answer the user's original question based on these images and the previous search results.",
                })
                messages.append({"role": "user", "content": content_blocks})

        else:
            break


    answer_parts: list[str] = []
    stream = await chat_with_retry(
        client,
        model=model,
        max_tokens=_QA_MAX_TOKENS,
        messages=messages,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            answer_parts.append(delta)
            await send_fn({"type": "qa_chunk", "text": delta})

    full_answer = "".join(answer_parts)
    insert_chat_message(video_id, "assistant", full_answer, playback_position,
                        conversation_id=conversation_id)
    await send_fn({"type": "qa_done"})
