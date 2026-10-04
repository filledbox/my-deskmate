import os
import asyncio
import locale
import shlex
import subprocess
import json
import aiofiles
from fastapi import UploadFile, HTTPException

from .preprocess import AUDIO_DIR

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
VIDEOS_DIR = os.path.join(DATA_DIR, "videos")
os.makedirs(VIDEOS_DIR, exist_ok=True)


async def _run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()


    enc = locale.getpreferredencoding(False)
    return subprocess.CompletedProcess(
        cmd, proc.returncode or 0,
        stdout=stdout.decode(enc, errors="replace"),
        stderr=stderr.decode(enc, errors="replace"),
    )


def _build_extra_args(cookies_path: str | None, extra_args: str | None) -> list[str]:

    args: list[str] = []
    cookies = cookies_path or os.getenv("YT_DLP_COOKIES")
    if cookies:
        args += ["--cookies", cookies]
    raw = extra_args if extra_args not in (None, "") else os.getenv("YT_DLP_EXTRA_ARGS")
    if raw:
        args += shlex.split(raw)
    return args


async def probe_video(url: str, cookies_path: str | None = None,
                      extra_args: str | None = None) -> tuple[str, str]:

    cmd = ["yt-dlp", "--no-playlist", "--skip-download",
           "--print", "%(extractor_key)s", "--print", "%(title)s"]
    cmd += _build_extra_args(cookies_path, extra_args) + [url]
    proc = await _run_subprocess(cmd)
    if proc.returncode != 0:
        raise HTTPException(500, f"yt-dlp failed: {proc.stderr}")
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln]
    extractor_key = lines[0] if lines else "generic"
    title = lines[1] if len(lines) > 1 else url.rsplit("/", 1)[-1][:100]
    return extractor_key, title


def normalize_source_type(extractor_key: str) -> str:

    name = (extractor_key or "generic").lower()
    name = "".join(c for c in name if c.isalnum() or c in "_")[:40]
    return name or "generic"


async def download_lowres_video(url: str, video_id: str, cookies_path: str | None = None,
                                extra_args: str | None = None) -> str | None:


    output_template = os.path.join(VIDEOS_DIR, f"{video_id}.%(ext)s")
    cmd = [
        "yt-dlp",

        "-f", "bv[height<=360]+ba/b[height<=360]/b",
        "-o", output_template,
        "--no-playlist",
        "--merge-output-format", "mp4",
    ]
    cmd += _build_extra_args(cookies_path, extra_args) + [url]
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

    output_template = os.path.join(AUDIO_DIR, f"{video_id}.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "bestaudio",
        "-x",
        "--audio-format", "m4a",
        "-o", output_template,
        "--no-playlist",
    ]
    cmd += _build_extra_args(cookies_path, extra_args) + [url]
    proc = await _run_subprocess(cmd)
    if proc.returncode != 0:
        raise HTTPException(500, f"yt-dlp failed: {proc.stderr}")

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
