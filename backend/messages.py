"""用户可见文案。

技术细节留在服务端日志和 preprocessing_progress.details；
这里只放简短、面向用户的说明。
"""

NO_SPEECH = "这节课没有讲解声，我暂时没法根据内容回答。"
NO_SPEECH_WITH_FRAMES = "这节课没有讲解声，我暂时只能根据画面回答。"
INCOMPLETE = "前面的内容我已经听好了，可以先问这部分；后面的内容刚才没转成，稍后可以再试一次。"
KEYFRAMES_SKIPPED = "这次没能读取视频画面，暂时只能根据讲解内容回答。"
GENERIC_ERROR = "这节课我没准备好，请稍后重新加载一次。"


def friendly_error(exc: BaseException) -> str:
    """把技术异常翻译成一句用户能看懂的话。"""
    text = f"{type(exc).__name__}: {exc}"
    lower = text.lower()

    if "asrconfig" in lower:
        return "我还没连上转写服务，请先检查一下设置。"
    if "额度" in text or "quota" in lower:
        return "今天的转写额度用完了，晚点再试，或者换一个转写服务。"
    if "567" in text or "412" in text:
        return "B 站暂时没让我读取这节课的音频，稍后再试一次吧；换个网络也可能有用。"
    if "ffmpeg" in lower or "ffprobe" in lower:
        return "这台电脑缺少音频处理组件，暂时没法处理视频。"
    if "yt-dlp" in lower or "找不到命令" in text:
        return "这个视频暂时没下载下来，稍后再试一次吧。"
    if "asr" in lower or "转写" in text:
        return "转写服务刚才没响应，前面已经听好的部分还可以继续问。"
    return GENERIC_ERROR


def friendly_stored(text: str) -> str:
    """兼容旧数据：库里已经是用户文案就原样返回，明显技术的才翻译。"""
    text = (text or "").strip()
    if not text:
        return GENERIC_ERROR
    markers = ("500:", "yt-dlp", "asr 未配置", "转写窗口", "找不到命令", "ffmpeg", "http error")
    if "asr 未配置" in text.lower():
        return "我还没连上转写服务，请先检查一下设置。"
    if any(marker in text.lower() for marker in markers):
        return friendly_error(RuntimeError(text))
    return text
