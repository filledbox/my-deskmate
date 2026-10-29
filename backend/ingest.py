import os
import asyncio
import locale
import re
import shlex
import subprocess
import json
from urllib.parse import urlsplit

import aiofiles
from fastapi import UploadFile, HTTPException

from .preprocess import AUDIO_DIR

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
VIDEOS_DIR = os.path.join(DATA_DIR, "videos")
os.makedirs(VIDEOS_DIR, exist_ok=True)

# B 站 CDN（upos-*.bilivideo.com）对不带 Range 的整段 GET 会返回 HTTP 567，
# 分块下载（每次请求带 Range）即可绕过；其它站点保持 yt-dlp 默认行为。
DEFAULT_BILIBILI_CHUNK_SIZE = "1M"
_BILIBILI_HOSTS = ("bilibili.com", "b23.tv")

_HTTP_ERROR_HINTS = {
    403: "CDN 拒绝访问（HTTP 403）",
    412: "B 站接口触发风控（HTTP 412）",
    429: "请求过于频繁（HTTP 429）",
    567: "B 站 CDN 触发风控（HTTP 567）",
}
_YTDLP_HINT = (
    "可尝试：重新点击 Load Video；在 Options 里填 cookies.txt，"
    "或在 .env 设置 YT_DLP_COOKIES / YT_DLP_COOKIES_FROM_BROWSER；"
    "升级 yt-dlp；切换网络或代理。"
)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_bilibili_url(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in _BILIBILI_HOSTS)


def chunk_size_for(url: str) -> str:
    """返回下载时使用的 --http-chunk-size；空字符串表示不分块。

    YT_DLP_CHUNK_SIZE 显式设置时优先（设为空字符串即关闭分块）。
    """
    configured = os.getenv("YT_DLP_CHUNK_SIZE")
    if configured is not None:
        return configured.strip()
    return DEFAULT_BILIBILI_CHUNK_SIZE if is_bilibili_url(url) else ""


def _chunk_args(url: str) -> list[str]:
    size = chunk_size_for(url)
    return ["--http-chunk-size", size] if size else []


def summarize_ytdlp_error(stderr: str, limit: int = 400) -> str:
    """把 yt-dlp 的 stderr 压成一行可展示的错误，并给常见风控码加上提示。"""
    text = (stderr or "").replace("\r", " ")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("ERROR:")]
    message = " ".join(errors or lines[-3:])
    message = re.sub(r"\s+", " ", message).strip()
    if len(message) > limit:
        message = message[: limit - 1] + "…"
    for code, label in _HTTP_ERROR_HINTS.items():
        if re.search(rf"HTTP (?:Error|error) {code}\b", message):
            detail = f"：{message}" if message else ""
            return f"{label}{detail}。{_YTDLP_HINT}"
    return message or "yt-dlp 执行失败，但没有输出错误信息"


async def _run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            500, f"找不到命令 {cmd[0]}，请先安装并确认它在 PATH 中"
        ) from exc
    stdout, stderr = await proc.communicate()


    enc = locale.getpreferredencoding(False)
    return subprocess.CompletedProcess(
        cmd, proc.returncode or 0,
        stdout=stdout.decode(enc, errors="replace"),
        stderr=stderr.decode(enc, errors="replace"),
    )


def _build_extra_args(cookies_path: str | None, extra_args: str | None) -> list[str]:
    """cookies.txt 路径、浏览器 cookies 与自定义 yt-dlp 参数。

    请求体优先，未给时回退 .env 配置。
    """
    args: list[str] = []
    cookies = (cookies_path or os.getenv("YT_DLP_COOKIES") or "").strip()
    if cookies:
        args += ["--cookies", os.path.expanduser(cookies)]
    browser = (os.getenv("YT_DLP_COOKIES_FROM_BROWSER") or "").strip()
    if browser:
        args += ["--cookies-from-browser", browser]
    raw = extra_args if extra_args not in (None, "") else os.getenv("YT_DLP_EXTRA_ARGS")
    if raw and raw.strip():
        args += shlex.split(raw)
    return args


async def probe_video(url: str, cookies_path: str | None = None,
                      extra_args: str | None = None) -> tuple[str, str]:
    """拉取元数据（不下载），返回 (extractor_key, title)。"""
    cmd = ["yt-dlp", "--no-playlist", "--skip-download",
           "--print", "%(extractor_key)s", "--print", "%(title)s"]
    cmd += _build_extra_args(cookies_path, extra_args) + [url]
    proc = await _run_subprocess(cmd)
    if proc.returncode != 0:
        raise HTTPException(
            500, f"yt-dlp 解析视频失败：{summarize_ytdlp_error(proc.stderr)}"
        )
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln]
    extractor_key = lines[0] if lines else "generic"
    title = lines[1] if len(lines) > 1 else url.rsplit("/", 1)[-1][:100]
    return extractor_key, title


def normalize_source_type(extractor_key: str) -> str:
    """把 yt-dlp extractor 名转成入库用的平台名，如 Bilibili -> bilibili。"""
    name = (extractor_key or "generic").lower()
    name = "".join(c for c in name if c.isalnum() or c in "_")[:40]
    return name or "generic"


async def download_lowres_video(url: str, video_id: str, cookies_path: str | None = None,
                                extra_args: str | None = None) -> str | None:
    """低清视频流（≤360p）供关键帧抽取，同时作为音频下载失败时的兜底。

    失败返回 None，不阻塞主流程。
    """
    output_template = os.path.join(VIDEOS_DIR, f"{video_id}.%(ext)s")
    cmd = [
        "yt-dlp",
        # bilibili 格式是「视频流/音频流分离」：best 必须带 +ba 合并，否则匹配不到。
        "-f", "bv[height<=360]+ba/b[height<=360]/b",
        "-o", output_template,
        "--no-playlist",
        "--merge-output-format", "mp4",
    ]
    cmd += _chunk_args(url) + _build_extra_args(cookies_path, extra_args) + [url]
    proc = await _run_subprocess(cmd)
    if proc.returncode != 0:
        return None

    video_path = os.path.join(VIDEOS_DIR, f"{video_id}.mp4")
    if not os.path.exists(video_path):
        for ext in ["mkv", "webm", "mp4"]:
            candidate = os.path.join(VIDEOS_DIR, f"{video_id}.{ext}")
            if os.path.exists(candidate):
                video_path = candidate
                break
        else:
            return None
    return video_path


async def download_audio(url: str, video_id: str, cookies_path: str | None = None,
                         extra_args: str | None = None) -> str:
    """音频优先下载：只取音轨并统一转成 m4a。"""
    output_template = os.path.join(AUDIO_DIR, f"{video_id}.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "bestaudio/best",
        "-x",
        "--audio-format", "m4a",
        "-o", output_template,
        "--no-playlist",
    ]
    cmd += _chunk_args(url) + _build_extra_args(cookies_path, extra_args) + [url]
    proc = await _run_subprocess(cmd)
    if proc.returncode != 0:
        raise HTTPException(
            500, f"yt-dlp 下载音频失败：{summarize_ytdlp_error(proc.stderr)}"
        )

    audio_path = os.path.join(AUDIO_DIR, f"{video_id}.m4a")
    if not os.path.exists(audio_path):
        for ext in ["opus", "webm", "aac"]:
            candidate = os.path.join(AUDIO_DIR, f"{video_id}.{ext}")
            if os.path.exists(candidate):
                audio_path = candidate
                break
        else:
            raise HTTPException(500, "Downloaded audio not found")
    return audio_path


async def save_uploaded_file(file: UploadFile, video_id: str) -> str:
    ext = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"
    video_path = os.path.join(VIDEOS_DIR, f"{video_id}{ext}")
    async with aiofiles.open(video_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            await f.write(chunk)
    return video_path


def get_video_duration(video_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 0.0
    data = json.loads(result.stdout)
    return float(data.get("format", {}).get("duration", 0))
