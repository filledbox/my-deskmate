import base64
import glob
import io
import os
import re
import shutil
import subprocess

from PIL import Image

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
AUDIO_DIR = os.path.join(DATA_DIR, "audio")
FRAMES_DIR = os.path.join(DATA_DIR, "frames")
os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(FRAMES_DIR, exist_ok=True)


def extract_audio(video_path: str, output_path: str | None = None) -> str:

    if output_path is None:
        basename = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(AUDIO_DIR, f"{basename}.wav")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr}")
    return output_path


def extract_keyframes(video_path: str, output_dir: str | None = None,
                      scene_threshold: float = 0.05,
                      min_interval: float = 20.0) -> list[dict]:


    if output_dir is None:
        basename = os.path.splitext(os.path.basename(video_path))[0]
        output_dir = os.path.join(FRAMES_DIR, basename)
    if os.path.isdir(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    output_pattern = os.path.join(output_dir, "frame_%04d.jpg")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"select='gt(scene,{scene_threshold})',showinfo",
        "-vsync", "vfr",
        "-q:v", "2",
        output_pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg keyframe extraction failed: {result.stderr}")

    paths = sorted(glob.glob(os.path.join(output_dir, "frame_*.jpg")))
    times = [float(m) for m in re.findall(r"pts_time:(\d+(?:\.\d+)?)", result.stderr)]

    kept = []
    last = -min_interval
    for i, path in enumerate(paths):
        ts = times[i] if i < len(times) else 0.0
        if ts - last >= min_interval:
            kept.append((ts, path))
            last = ts
        else:
            try:
                os.remove(path)
            except OSError:
                pass

    if not kept:
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", "fps=1/30", "-q:v", "2", output_pattern]
        subprocess.run(cmd, capture_output=True, text=True)
        paths = sorted(glob.glob(os.path.join(output_dir, "frame_*.jpg")))
        kept = [(i * 30 + 15, p) for i, p in enumerate(paths)]

    return [{"timestamp": ts, "path": path} for ts, path in kept]


def prepare_frame_for_api(image_path: str, max_size: int = 768) -> str:


    img = Image.open(image_path)
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()
