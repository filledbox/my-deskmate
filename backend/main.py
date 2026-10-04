import os
import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from dotenv import load_dotenv

load_dotenv()

from .memory import (
    init_db, insert_video, update_video_status, update_video_duration,
    update_video_file_path, persist_transcript, insert_keyframes,
    update_progress, get_latest_progress, get_video,
    get_video_by_url, claim_video_retry, get_chapters, insert_chapters, get_all_transcript_segments,
    get_transcription_coverage, get_preprocessing_videos,
    count_keyframes, normalize_video_url,
    media_file_exists, resolve_media_path,
    create_conversation, get_conversation, get_conversation_for_video,
    list_conversations, get_conversation_messages, delete_conversation, derive_title,
)
from .ingest import (
    probe_video, download_audio, download_lowres_video,
    normalize_source_type, save_uploaded_file, get_video_duration,
)
from .preprocess import extract_audio, extract_keyframes, AUDIO_DIR
from .asr import (
    ASRQuotaExhaustedError, ASRWindowError,
    transcribe_full, transcribe_window,
    TRANSCRIBE_WINDOW_SECONDS, TRANSCRIBE_OVERLAP_SECONDS,
)
from .chapters import segment_into_chapters
from .qa import stream_answer, stream_answer_agentic

BACKEND_DIR = os.path.dirname(__file__)
FRONTEND_DIR = os.path.join(BACKEND_DIR, "..", "frontend")

active_connections: dict[str, list[WebSocket]] = {}


def _progress_payload(video: dict | None, progress: dict | None) -> dict:


    status = video["status"] if video else "unknown"
    total = (video.get("duration_seconds") or 0) if video else 0
    if status == "ready" and not progress:
        return {
            "type": "progress",
            "stage": "complete",
            "processed_up_to": total,
            "total": total,
            "status": status,
        }
    return {
        "type": "progress",
        "stage": progress["stage"] if progress else "pending",
        "processed_up_to": progress["processed_up_to_seconds"] if progress else 0,
        "total": total,
        "status": status,
    }


async def broadcast_progress(video_id: str, data: dict):
    for ws in active_connections.get(video_id, []):
        try:
            await ws.send_json({"type": "progress", **data})
        except Exception:
            pass


async def transcribe_progressively(video_id: str, audio_path: str, duration: float) -> float:


    if not duration or duration <= 0:

        segments = await transcribe_full(audio_path)
        if segments:
            await asyncio.to_thread(persist_transcript, video_id, segments)
            coverage = segments[-1]["end"]
            update_progress(video_id, "transcription", coverage, coverage)
            await broadcast_progress(video_id, {
                "stage": "transcription", "processed_up_to": coverage, "total": coverage,
            })
        return get_transcription_coverage(video_id)

    max_attempts = int(os.getenv("ASR_WINDOW_MAX_ATTEMPTS", "3"))
    coverage = get_transcription_coverage(video_id)
    failures = 0
    while coverage < duration:
        clip_start = max(0.0, coverage - TRANSCRIBE_OVERLAP_SECONDS)
        clip_end = min(duration, coverage + TRANSCRIBE_WINDOW_SECONDS)
        if clip_end - clip_start < 1.0:
            break

        try:
            segments = await transcribe_window(
                audio_path, clip_start, clip_end - clip_start
            )
        except ASRQuotaExhaustedError:

            raise
        except ASRWindowError as exc:
            if not exc.transient:


                coverage = clip_end
                failures = 0
                update_progress(video_id, "transcription_warning", coverage, duration,
                                f"{exc.window_start:.0f}s 起无音频，已跳过")
                await broadcast_progress(video_id, {
                    "stage": "transcription_warning", "processed_up_to": coverage,
                    "total": duration,
                    "message": f"{exc.window_start:.0f}s 之后没有可转写的音频",
                })
                continue

            failures += 1
            if failures >= max_attempts:


                raise
            update_progress(
                video_id, "transcription_retry", coverage, duration,
                f"{clip_start:.0f}–{clip_end:.0f}s 第 {failures}/{max_attempts} 次重试",
            )
            await broadcast_progress(video_id, {
                "stage": "transcription_retry", "processed_up_to": coverage,
                "total": duration,
                "message": f"转写 {clip_start:.0f}s 处失败，正在重试"
                           f"（{failures}/{max_attempts}）",
            })
            await asyncio.sleep(min(30.0, 5.0 * failures))
            continue

        if segments:


            await asyncio.to_thread(persist_transcript, video_id, segments)

        coverage = clip_end
        failures = 0
        update_progress(video_id, "transcription", coverage, duration)
        await broadcast_progress(video_id, {
            "stage": "transcription", "processed_up_to": coverage, "total": duration,
        })

    return coverage


def _short_error(msg: str, limit: int = 600) -> str:


    msg = (msg or "").strip()
    if len(msg) <= limit:
        return msg
    half = limit // 2
    return f"{msg[:half]}\n…（略去 {len(msg) - 2 * half} 字）…\n{msg[-half:]}"


async def run_preprocessing_pipeline(video_id: str, cookies_path: str | None = None,
                                     extra_args: str | None = None):


    video = get_video(video_id)
    if not video:
        return
    is_local = video["source_type"] == "local"
    video_path = video["file_path"]

    try:
        update_video_status(video_id, "preprocessing")

        if is_local:
            update_progress(video_id, "audio_extraction", 0, 0, "Extracting audio...")
            await broadcast_progress(video_id, {"stage": "audio_extraction", "processed_up_to": 0, "total": 0})
            audio_path = await asyncio.to_thread(extract_audio, video_path)
        else:

            audio_path = os.path.join(AUDIO_DIR, f"{video_id}.m4a")
            if not os.path.exists(audio_path):
                update_progress(video_id, "downloading", 0, 0, "Downloading audio...")
                await broadcast_progress(video_id, {"stage": "downloading", "processed_up_to": 0, "total": 0})
                audio_path = await download_audio(video["source_url"], video_id, cookies_path, extra_args)


        duration = get_video_duration(audio_path)
        update_video_duration(video_id, duration)
        update_progress(video_id, "start", 0, duration)
        await broadcast_progress(video_id, {"stage": "starting", "processed_up_to": 0, "total": duration})


        lowres_task = None
        if not is_local:
            already_have_frames = count_keyframes(video_id) > 0
            have_lowres_file = bool(video_path) and os.path.exists(video_path)
            if not already_have_frames and not have_lowres_file:
                lowres_task = asyncio.create_task(
                    download_lowres_video(video["source_url"], video_id, cookies_path, extra_args)
                )


        initial_coverage = get_transcription_coverage(video_id)
        update_progress(video_id, "transcription", initial_coverage, duration, "Transcribing speech...")
        await broadcast_progress(video_id, {"stage": "transcription", "processed_up_to": initial_coverage, "total": duration})

        try:
            await transcribe_progressively(video_id, audio_path, duration)
        except (ASRQuotaExhaustedError, ASRWindowError) as exc:
            covered = get_transcription_coverage(video_id)
            if covered <= 0:
                raise


            short = _short_error(str(exc))
            update_progress(video_id, "transcription_incomplete", covered, duration,
                            f"已转写 {covered:.0f}s / {duration:.0f}s：{short}")
            await broadcast_progress(video_id, {
                "stage": "transcription_incomplete",
                "processed_up_to": covered, "total": duration,
                "message": f"转写中断在 {covered:.0f}s（共 {duration:.0f}s），"
                           "已覆盖部分仍可提问",
            })
        segments = get_all_transcript_segments(video_id)


        try:
            if segments:
                chapters = await segment_into_chapters(segments)
                if chapters:
                    insert_chapters(video_id, chapters)
                    update_progress(video_id, "chapters", 0, 0, f"{len(chapters)} chapters")
        except Exception as e:
            update_progress(video_id, "chapters", 0, 0, f"chapters failed: {e}")


        frames: list[dict] = []
        try:
            if is_local:
                update_progress(video_id, "keyframe_extraction", 0, duration, "Extracting keyframes...")
                await broadcast_progress(video_id, {"stage": "keyframe_extraction", "processed_up_to": 0, "total": duration})
                frames = await asyncio.to_thread(extract_keyframes, video_path)
            else:
                if count_keyframes(video_id) > 0:
                    frames = []
                else:
                    lowres_path = video_path if have_lowres_file else await lowres_task
                    if lowres_path:
                        update_video_file_path(video_id, lowres_path)
                        update_progress(video_id, "keyframe_extraction", 0, duration, "Extracting keyframes...")
                        await broadcast_progress(video_id, {"stage": "keyframe_extraction", "processed_up_to": 0, "total": duration})
                        frames = await asyncio.to_thread(extract_keyframes, lowres_path)
        except Exception as e:
            update_progress(video_id, "keyframes_skipped", duration, duration,
                            f"keyframes skipped: {_short_error(str(e), 200)}")
            await broadcast_progress(video_id, {
                "stage": "keyframes_skipped", "processed_up_to": duration, "total": duration,
                "message": "未能抽取关键帧，本视频以纯语音模式问答（画面相关问题会答不了）。",
            })

        if frames:
            insert_keyframes(video_id, frames)


        update_progress(video_id, "complete", duration, duration)
        update_video_status(video_id, "ready")
        await broadcast_progress(video_id, {"stage": "complete", "processed_up_to": duration, "total": duration})

    except Exception as e:
        short = _short_error(str(e))
        update_video_status(video_id, "error", short)
        update_progress(video_id, "error", 0, 0, short)
        await broadcast_progress(video_id, {"stage": "error", "message": short})


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    for v in get_preprocessing_videos():
        asyncio.create_task(run_preprocessing_pipeline(v["id"]))
    yield


app = FastAPI(lifespan=lifespan)


_DEFAULT_CORS_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
_extra_cors = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEFAULT_CORS_ORIGINS + _extra_cors,
    allow_origin_regex=r"https://.*\.bilibili\.com",


    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
    allow_credentials=False,
)


@app.post("/api/videos")
async def create_video(url: str = Form(None), file: UploadFile = File(None),
                       cookies_path: str = Form(None), extra_args: str = Form(None)):
    if not url and not file:
        raise HTTPException(400, "Either 'url' or 'file' is required")

    if url:


        url = normalize_video_url(url)
        existing = get_video_by_url(url)
        if existing:
            retry_started = False
            if existing["status"] == "error":
                retry_started = claim_video_retry(existing["id"])
                if retry_started:
                    asyncio.create_task(
                        run_preprocessing_pipeline(existing["id"], cookies_path, extra_args)
                    )
            return {
                "video_id": existing["id"],
                "title": existing["title"],
                "reused": True,
                "retry_started": retry_started,
            }
        extractor_key, title = await probe_video(url, cookies_path, extra_args)
        video_id = str(uuid.uuid4())[:8]
        video_path = ""
        source_type = normalize_source_type(extractor_key)
        source_url = url
    else:
        title = file.filename or "uploaded_video"
        video_id = str(uuid.uuid4())[:8]
        video_path = await save_uploaded_file(file, video_id)
        source_type = "local"
        source_url = None

    insert_video(video_id, title, source_type, source_url, video_path)

    asyncio.create_task(run_preprocessing_pipeline(video_id, cookies_path, extra_args))

    return {
        "video_id": video_id,
        "title": title,
        "reused": False,
        "retry_started": False,
    }


@app.get("/api/videos/{video_id}")
async def get_video_info(video_id: str):
    video = get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    return {
        "id": video["id"],
        "title": video["title"],
        "status": video["status"],
        "duration_seconds": video["duration_seconds"],
        "error_message": video.get("error_message"),
        "file_ready": media_file_exists(video.get("file_path")),
    }


@app.get("/api/videos/{video_id}/file")
async def serve_video(video_id: str):
    video = get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")


    path = resolve_media_path(video.get("file_path"))
    if not path:
        raise HTTPException(409, "视频文件尚未就绪（低清流仍在下载）")
    if not os.path.exists(path):
        raise HTTPException(404, "视频文件不存在（可能已被清理）")
    media_type = {
        ".mp4": "video/mp4",
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
        ".opus": "audio/ogg",
        ".aac": "audio/aac",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
    }.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


@app.get("/api/videos/{video_id}/chapters")
async def get_video_chapters(video_id: str):
    video = get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    chapters = get_chapters(video_id)
    return {
        "video_id": video_id,
        "duration_seconds": video["duration_seconds"],
        "chapters": chapters,
    }


@app.get("/api/videos/{video_id}/progress")
async def get_progress(video_id: str):
    video = get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found")
    progress = get_latest_progress(video_id)
    return {
        "status": video["status"],
        "progress": progress,
    }


CONVERSATION_REPLAY_LIMIT = 200


@app.get("/api/videos/{video_id}/conversations")
async def list_video_conversations(video_id: str):
    if not get_video(video_id):
        raise HTTPException(404, "Video not found")
    return {"video_id": video_id, "conversations": list_conversations(video_id)}


@app.get("/api/videos/{video_id}/conversations/{conversation_id}")
async def get_video_conversation(video_id: str, conversation_id: str, limit: int | None = None):


    conv = get_conversation_for_video(video_id, conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return {**conv, "messages": get_conversation_messages(conversation_id, limit)}


@app.delete("/api/videos/{video_id}/conversations/{conversation_id}")
async def delete_video_conversation(video_id: str, conversation_id: str):
    deleted = delete_conversation(video_id, conversation_id)
    if deleted is None:
        raise HTTPException(404, "Conversation not found")
    return {"deleted": True, "messages_deleted": deleted}


@app.websocket("/ws/{video_id}")
async def websocket_endpoint(websocket: WebSocket, video_id: str):
    await websocket.accept()
    active_connections.setdefault(video_id, []).append(websocket)


    progress = get_latest_progress(video_id)
    video = get_video(video_id)
    if video:
        await websocket.send_json(_progress_payload(video, progress))


    conversation_id: str | None = None

    try:
        while True:
            msg = await websocket.receive_json()

            if msg.get("type") == "ping":
                progress = get_latest_progress(video_id)
                video = get_video(video_id)
                await websocket.send_json(_progress_payload(video, progress))

            elif msg.get("type") == "qa_request":
                question = msg.get("question", "")
                position = msg.get("position", 0)

                if question.strip():


                    requested = msg.get("conversation_id")
                    if requested and requested != conversation_id:
                        conversation_id = (
                            requested if get_conversation_for_video(video_id, requested) else None
                        )
                    created = False
                    if conversation_id is None:
                        conversation_id = create_conversation(
                            video_id, derive_title(question)
                        )["id"]
                        created = True


                    await websocket.send_json({
                        "type": "conversation",
                        "conversation_id": conversation_id,
                        "video_id": video_id,
                        "title": (get_conversation(conversation_id) or {}).get("title"),
                        "created": created,
                    })


                    try:
                        if msg.get("agentic"):
                            await stream_answer_agentic(video_id, position, question,
                                                        websocket.send_json,
                                                        conversation_id=conversation_id)
                        else:
                            await stream_answer(video_id, position, question,
                                                websocket.send_json,
                                                conversation_id=conversation_id)
                    except Exception as exc:


                        detail = str(exc).strip() or type(exc).__name__
                        await websocket.send_json({
                            "type": "qa_warning",
                            "message": f"LLM 配置或调用失败：{detail}",
                        })
                        await websocket.send_json({"type": "qa_done"})


    except WebSocketDisconnect:
        pass
    finally:
        conns = active_connections.get(video_id)
        if conns is not None:


            try:
                conns.remove(websocket)
            except ValueError:
                pass
            if not conns:
                active_connections.pop(video_id, None)


if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
