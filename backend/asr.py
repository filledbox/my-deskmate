


import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from openai import (
    APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI,
    RateLimitError,
)


TRANSCRIBE_WINDOW_SECONDS = float(os.getenv("TRANSCRIBE_WINDOW_SECONDS", "300"))
TRANSCRIBE_OVERLAP_SECONDS = float(os.getenv("TRANSCRIBE_OVERLAP_SECONDS", "30"))


MAX_SEGMENT_SECONDS = 60.0


_FALLBACK_GRID_SECONDS = 60

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "whisper-1"
DEFAULT_TIMEOUT = 300.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF = 3.0
DEFAULT_MAX_UPLOAD_MB = 25
DEFAULT_MAX_CONCURRENCY = 2


_QUOTA_MARKERS = (
    "per day",
    "daily",
    "quota exceeded",
    "insufficient credits",
    "exceeded your current quota",
)


_OPTIONAL_PARAMS = ("language", "response_format", "prompt", "temperature")


_PUNCT = "，。！？；：、,.!?;:）)】」”"


_RUNTIME_METRICS = {
    "upload_attempts": 0,
    "upload_successes": 0,
    "upload_bytes": 0,
    "retry_count": 0,
    "degraded_windows": 0,
}


def reset_runtime_metrics() -> None:

    for key in _RUNTIME_METRICS:
        _RUNTIME_METRICS[key] = 0
    _DEGRADED_FLAG["value"] = False


def runtime_metrics() -> dict[str, int]:

    return dict(_RUNTIME_METRICS)


class ASRConfigError(RuntimeError):
    pass


class ASRQuotaExhaustedError(RuntimeError):
    pass


class ASRWindowError(RuntimeError):


    def __init__(self, window_start: float, window_end: float, cause: str,
                 *, transient: bool = False):
        self.window_start = window_start
        self.window_end = window_end
        self.transient = transient
        super().__init__(
            f"转写窗口 [{window_start:.1f}s, {window_end:.1f}s] 失败：{cause}"
        )


class ASRAudioEnded(ASRWindowError):
    """该窗口没有可解码音频：音频到此为止，不是失败。"""


@dataclass(frozen=True)
class ASRConfig:
    model: str
    base_url: str
    api_key: str
    language: str | None
    response_format: str
    slice_format: str
    max_upload_bytes: int
    timeout: float
    max_attempts: int
    backoff: float
    max_concurrency: int
    tmp_dir: str

    def client(self) -> AsyncOpenAI:

        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=0,
        )


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _default_tmp_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "audio", "_slices")


def resolve() -> ASRConfig:


    base_url = _env("ASR_BASE_URL")
    api_key = _env("ASR_API_KEY")
    if not base_url or not api_key:
        raise ASRConfigError(
            "ASR 未配置或只配了一半：必须同时设置 ASR_BASE_URL 与 ASR_API_KEY"
            f"（当前缺 {'ASR_BASE_URL' if not base_url else 'ASR_API_KEY'}）。"
            "例：ASR_BASE_URL=https://api.groq.com/openai/v1 + "
            "ASR_MODEL=whisper-large-v3-turbo"
        )

    fmt = _env("ASR_SLICE_FORMAT", "wav").lower()
    if fmt not in ("wav", "flac", "mp3"):
        raise ASRConfigError(
            f"ASR_SLICE_FORMAT={fmt!r} 不认识，可用：wav | flac | mp3"
        )
    response_format = _env("ASR_RESPONSE_FORMAT", "verbose_json")
    if response_format not in ("json", "verbose_json", "text"):
        raise ASRConfigError(
            f"ASR_RESPONSE_FORMAT={response_format!r} 不认识，"
            "可用：json | verbose_json | text"
        )

    max_upload_mb = float(_env("ASR_MAX_UPLOAD_MB", str(DEFAULT_MAX_UPLOAD_MB)))
    max_upload_bytes = int(max_upload_mb * 1_000_000)


    worst_window = TRANSCRIBE_WINDOW_SECONDS + TRANSCRIBE_OVERLAP_SECONDS
    bytes_per_sec = {"wav": 32_000, "flac": 16_000, "mp3": 8_000}[fmt]
    est = worst_window * bytes_per_sec
    if est > max_upload_bytes:
        raise ASRConfigError(
            f"切片太大：TRANSCRIBE_WINDOW_SECONDS({TRANSCRIBE_WINDOW_SECONDS:g}) + "
            f"TRANSCRIBE_OVERLAP_SECONDS({TRANSCRIBE_OVERLAP_SECONDS:g}) = "
            f"{worst_window:g}s，按 {fmt} 约 {est / 1e6:.1f}MB，"
            f"超过 ASR_MAX_UPLOAD_MB={max_upload_mb:g}。"
            "调小窗口或改用 flac/mp3。"
        )

    return ASRConfig(
        model=_env("ASR_MODEL", DEFAULT_MODEL),
        base_url=base_url,
        api_key=api_key,


        language=_env("ASR_LANGUAGE", "zh") or None,
        response_format=response_format,
        slice_format=fmt,
        max_upload_bytes=max_upload_bytes,
        timeout=float(_env("ASR_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT))),
        max_attempts=int(_env("ASR_RETRY_MAX_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS))),
        backoff=float(_env("ASR_RETRY_BACKOFF", str(DEFAULT_BACKOFF))),
        max_concurrency=int(_env("ASR_MAX_CONCURRENCY", str(DEFAULT_MAX_CONCURRENCY))),
        tmp_dir=_env("ASR_TMP_DIR") or _default_tmp_dir(),
    )


def make_client() -> AsyncOpenAI:
    return resolve().client()


def slice_audio(audio_path: str, start: float, duration: float,
                out_path: str | None = None,
                cfg: ASRConfig | None = None) -> str:


    cfg = cfg or resolve()
    if duration <= 0:
        raise ASRWindowError(start, start, "切片时长非正数")

    if out_path is None:
        os.makedirs(cfg.tmp_dir, exist_ok=True)


        fd, out_path = tempfile.mkstemp(suffix=f".{cfg.slice_format}", dir=cfg.tmp_dir)
        os.close(fd)

    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}",
        "-i", audio_path,
        "-t", f"{duration:.3f}",
        "-vn",
        "-ac", "1", "-ar", "16000",
    ]
    if cfg.slice_format == "wav":
        cmd += ["-c:a", "pcm_s16le", "-f", "wav"]
    elif cfg.slice_format == "flac":
        cmd += ["-c:a", "flac", "-f", "flac"]
    else:
        cmd += ["-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3"]
    cmd.append(out_path)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise ASRWindowError(start, start + duration,
                             "找不到 ffmpeg，请确认它在 PATH 里") from exc

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        raise ASRWindowError(start, start + duration,
                             f"ffmpeg 切片失败：{err[-500:] or proc.returncode}")


    if os.path.getsize(out_path) <= 44:
        os.unlink(out_path)
        raise ASRAudioEnded(start, start + duration, "切片为空（没有音频流？）")
    return out_path


def _seg_get(seg, name: str, default=None):

    if isinstance(seg, dict):
        return seg.get(name, default)
    return getattr(seg, name, default)


def _backfill(segs: list[dict], offset: float, clip_end: float) -> list[dict]:


    out = []
    for s in segs:
        text = (_seg_get(s, "text", "") or "").strip()
        if not text:
            continue
        rel_start = _seg_get(s, "start")
        rel_end = _seg_get(s, "end")
        if rel_start is None or rel_end is None:
            continue
        abs_start = round(min(max(offset + float(rel_start), offset), clip_end), 2)
        abs_end = round(min(max(offset + float(rel_end), offset), clip_end), 2)
        if abs_end <= abs_start:
            continue
        out.append({"start": abs_start, "end": abs_end, "text": text})
    out.sort(key=lambda x: x["start"])
    return out


def _segments_usable(segs: list[dict]) -> bool:


    if len(segs) < 2:


        return False
    starts = [s["start"] for s in segs]
    if any(s["end"] <= s["start"] for s in segs):
        return False
    if any(b < a for a, b in zip(starts, starts[1:])):
        return False
    return not all(abs(v) < 1e-6 for v in starts)


def _aligned_bounds(t_start: float, t_end: float) -> list[tuple[float, float]]:


    bounds = []
    cur = t_start
    while cur < t_end - 1e-6:
        nxt = float((int(cur) // _FALLBACK_GRID_SECONDS + 1) * _FALLBACK_GRID_SECONDS)
        nxt = min(nxt, t_end)
        if nxt <= cur:
            nxt = t_end
        bounds.append((cur, nxt))
        cur = nxt
    return bounds


def _snap_cut(text: str, pos: int, target: int, slack: int) -> int:

    lo = max(pos, target - slack)
    hi = min(len(text), target + slack)
    best = None
    for i in range(lo, hi):
        if text[i] in _PUNCT:
            cut = i + 1
            if cut <= pos:
                continue
            dist = abs(cut - target)
            if best is None or dist < best[0]:
                best = (dist, cut)
    return best[1] if best else target


def _split_text_over_windows(text: str, t_start: float, t_end: float) -> list[dict]:


    text = (text or "").strip()
    if not text or t_end - t_start < 1e-6:
        return []
    bounds = _aligned_bounds(t_start, t_end)
    if not bounds:
        return [{"start": round(t_start, 2), "end": round(t_end, 2), "text": text}]

    total = t_end - t_start
    n = len(text)
    slack = max(4, n // max(len(bounds), 1) // 5)
    out = []
    pos = 0
    for idx, (ws, we) in enumerate(bounds):
        if idx == len(bounds) - 1:
            cut = n
        else:
            target = max(pos, round(n * (we - t_start) / total))
            cut = max(pos, _snap_cut(text, pos, target, slack))
        chunk = text[pos:cut].strip()
        pos = cut
        if not chunk:
            continue
        out.append({
            "start": round(min(max(ws, t_start), t_end), 2),
            "end": round(min(max(we, t_start), t_end), 2),
            "text": chunk,
        })
    return out


def _enforce_max_segment(segs: list[dict]) -> list[dict]:

    out = []
    for s in segs:
        if s["end"] - s["start"] > MAX_SEGMENT_SECONDS:
            out.extend(_split_text_over_windows(s["text"], s["start"], s["end"]))
        else:
            out.append(s)
    return out


def _supersede(kept: list[dict], new: list[dict]) -> list[dict]:


    if not new:
        return kept
    from_sec = min(s["start"] for s in new)
    to_sec = max(s["end"] for s in new)
    return [
        s for s in kept
        if not (s["end"] > from_sec and s["start"] <= to_sec)
    ] + new


_SEMAPHORES: dict = {}


def _semaphore(limit: int) -> asyncio.Semaphore:


    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    sem = _SEMAPHORES.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(max(1, limit))
        _SEMAPHORES[loop] = sem
    return sem


def _err_text(exc: BaseException) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    return f"{body or ''} {exc}".lower()


def _is_quota_exhausted(exc: RateLimitError) -> bool:
    text = _err_text(exc)
    return any(m in text for m in _QUOTA_MARKERS)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (RateLimitError, APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return 500 <= getattr(exc, "status_code", 0) < 600
    return False


def _unsupported_param(exc: BaseException) -> str | None:

    if not isinstance(exc, APIStatusError) or getattr(exc, "status_code", 0) != 400:
        return None
    text = _err_text(exc)
    for name in _OPTIONAL_PARAMS:
        if name in text and any(
            k in text for k in ("unsupported", "unrecognized", "unknown",
                                "invalid", "not supported", "extra")
        ):
            return name
    return None


async def _upload(cfg: ASRConfig, wav_path: str, *, drop: str | None = None):

    kwargs: dict = {"model": cfg.model, "response_format": cfg.response_format}
    if drop != "language" and cfg.language:
        kwargs["language"] = cfg.language
    if drop == "response_format":
        kwargs["response_format"] = "json"

    client = cfg.client()
    async with _semaphore(cfg.max_concurrency):
        _RUNTIME_METRICS["upload_attempts"] += 1
        _RUNTIME_METRICS["upload_bytes"] += os.path.getsize(wav_path)
        with open(wav_path, "rb") as fh:
            result = await client.audio.transcriptions.create(file=fh, **kwargs)
        _RUNTIME_METRICS["upload_successes"] += 1
        return result


async def _transcribe_span(audio_path: str, start: float, end: float, *,
                           cfg: ASRConfig, depth: int = 0) -> tuple[list[dict], bool]:


    duration = end - start
    if duration < 0.5:
        return [], False


    wav = await asyncio.to_thread(slice_audio, audio_path, start, duration, None, cfg)

    try:
        drop: str | None = None
        last_exc: BaseException | None = None
        for attempt in range(cfg.max_attempts):
            try:
                resp = await _upload(cfg, wav, drop=drop)
                last_exc = None
                break
            except RateLimitError as exc:
                if _is_quota_exhausted(exc):
                    raise ASRQuotaExhaustedError(
                        f"ASR 额度已耗尽（非每分钟限流，重试无意义）：{exc}"
                    ) from exc
                last_exc = exc
            except APIStatusError as exc:

                code = getattr(exc, "status_code", 0)
                text = _err_text(exc)
                if depth < 2 and (
                    code == 413 or (code == 400 and any(
                        k in text for k in ("too large", "too long", "maximum",
                                            "size", "duration"))
                    )
                ):
                    mid = start + duration / 2
                    a, da = await _transcribe_span(audio_path, start, mid,
                                                   cfg=cfg, depth=depth + 1)
                    b, db = await _transcribe_span(audio_path, mid, end,
                                                   cfg=cfg, depth=depth + 1)
                    return _supersede(a, b), (da or db)
                bad = _unsupported_param(exc)
                if bad and drop != bad:
                    drop = bad
                    last_exc = exc
                elif not _is_retryable(exc):
                    raise ASRWindowError(start, end, f"HTTP {code}：{exc}") from exc
                else:
                    last_exc = exc
            except (APIConnectionError, APITimeoutError) as exc:
                last_exc = exc

            if attempt < cfg.max_attempts - 1:
                _RUNTIME_METRICS["retry_count"] += 1
                await asyncio.sleep(cfg.backoff * (2 ** attempt))

        if last_exc is not None:
            raise ASRWindowError(
                start, end, f"重试 {cfg.max_attempts} 次仍失败：{last_exc}",
                transient=True,
            )

        text = (_seg_get(resp, "text", "") or "").strip()
        raw = _seg_get(resp, "segments", None) or []
        segs = _backfill(list(raw), start, end)

        if not _segments_usable(segs):


            if not text:
                return [], False
            segs = _split_text_over_windows(text, start, end)
            _RUNTIME_METRICS["degraded_windows"] += 1
            return _enforce_max_segment(segs), True
        return _enforce_max_segment(segs), False
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass


async def transcribe_window(audio_path: str, offset: float,
                            duration: float) -> list[dict]:

    cfg = resolve()
    segs, _ = await _transcribe_span(audio_path, offset, offset + duration, cfg=cfg)
    return segs


async def transcribe_full(audio_path: str, *,
                          duration: float | None = None) -> list[dict]:


    cfg = resolve()
    total = duration if duration is not None else await asyncio.to_thread(
        probe_duration, audio_path
    )
    if not total or total <= 0:
        return await _all_at_once(audio_path, cfg)

    kept: list[dict] = []
    coverage = 0.0
    while coverage < total:
        clip_start = max(0.0, coverage - TRANSCRIBE_OVERLAP_SECONDS)
        clip_end = min(total, coverage + TRANSCRIBE_WINDOW_SECONDS)
        if clip_end - clip_start < 1.0:
            break
        try:
            segs, degraded = await _transcribe_span(audio_path, clip_start, clip_end,
                                                    cfg=cfg)
        except ASRAudioEnded:
            break
        kept = _supersede(kept, segs)
        coverage = clip_end
        if degraded:
            _mark_degraded()
    if not kept:
        return await _all_at_once(audio_path, cfg)
    return kept


async def _all_at_once(audio_path: str, cfg: ASRConfig) -> list[dict]:

    try:
        segs, _ = await _transcribe_span(audio_path, 0.0, 1e9, cfg=cfg)
    except ASRAudioEnded:
        return []
    return segs


_DEGRADED_FLAG = {"value": False}


def _mark_degraded() -> None:


    if not _DEGRADED_FLAG["value"]:
        _DEGRADED_FLAG["value"] = True
        print("[asr] 警告：服务器未返回可信分段，时间戳已按比例摊到 60s 网格上"
              "（[MM:SS] 引用精度降为 ±60s）")


def degraded_in_this_process() -> bool:
    return _DEGRADED_FLAG["value"]


def probe_duration(audio_path: str) -> float:


    ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", audio_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return 0.0
    if proc.returncode != 0:
        return 0.0
    try:
        return float((proc.stdout or "").strip())
    except ValueError:
        return 0.0
