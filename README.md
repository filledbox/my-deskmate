# MyDeskmate

MyDeskmate 是一个面向 B 站网课的视频问答助手。它将视频音频分窗转写，生成章节和时间索引；提问时结合播放位置、语义检索结果和章节内容生成回答。回答中的时间戳可以点击跳转，涉及板书或画面的问题可按需读取附近关键帧。

## 功能

- 渐进式转写：已处理的视频片段可以先用于问答；中断后从已保存的进度继续。
- 视频检索：结合当前播放位置、60 秒转写窗口与章节内容组织问答上下文。
- 多轮对话：按视频保存并隔离会话，可切换和删除历史对话。
- 画面问答：从播放位置附近的关键帧获取视觉上下文。
- 时间戳引用：回答中的 `[MM:SS]` 可跳转到对应播放位置。

## 技术组成

| 组件 | 用途 |
| --- | --- |
| FastAPI、WebSocket | 提供视频处理、问答与进度接口 |
| FFmpeg、yt-dlp | 提取音频、获取视频及关键帧 |
| OpenAI 兼容 ASR 接口 | 生成带时间戳的转写片段 |
| SQLite、Chroma | 保存视频数据、对话和向量索引 |
| OpenAI 兼容多模态模型接口 | 章节生成、文本与画面问答 |
| 浏览器扩展（Manifest V3） | 在 B 站播放器旁显示悬浮问答面板 |

## 本地运行

需要 Python 3.11、FFmpeg，以及可用的 ASR 和多模态模型 API。模型需要支持图片输入；ASR 服务需要支持 `verbose_json` 和分段时间戳，才能提供精确引用。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 中填写 `LLM_API_KEY` 和 `ASR_API_KEY`，按实际服务修改对应的 URL 与模型名，然后启动：

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

在 Chrome 或其他支持 Manifest V3 的浏览器中打开扩展管理页，启用开发者模式，选择“加载已解压的扩展程序”并选中 `extension/`。进入 B 站视频页后点击悬浮球即可使用。

数据默认保存在本机的 `data/`，该目录不会进入 Git。使用云端服务时，音频会发送至 `.env` 指定的 ASR 服务；提问内容、检索到的转写文本和按需选取的关键帧会发送至指定的模型服务。请按视频内容的使用权限和服务商的数据政策配置。

## 常见问题：B 站下载失败（HTTP 412 / 567）

启动转写时如果状态栏出现 `yt-dlp 下载音频失败：B 站 CDN 触发风控（HTTP 567）` 或 `HTTP 412`，说明请求被 B 站风控拦截，和 API Key、转写服务无关。

本项目默认对 B 站链接启用分块下载（`--http-chunk-size 1M`）：B 站 CDN 对不带 `Range` 的整段 GET 会返回 567，而分块请求（带 `Range`）通常能正常返回 206。若仍然失败，按顺序尝试：

1. **升级 yt-dlp**：`pip install -U yt-dlp`。B 站接口改动频繁，旧版本最容易失效。
2. **配置 cookies**：登录 B 站后导出 `cookies.txt`，在页面 Options 里填路径，或写入 `.env`：
   ```bash
   YT_DLP_COOKIES=~/cookies.txt
   ```
   也可以让 yt-dlp 直接读取浏览器 cookies（macOS 可能需要在钥匙串中允许访问）：
   ```bash
   YT_DLP_COOKIES_FROM_BROWSER=chrome
   ```
3. **换网络或代理**：例如 `YT_DLP_EXTRA_ARGS="--proxy http://127.0.0.1:7890"`；或者等待几分钟再重试。
4. **原位重试**：在页面再次点击 Load Video，会对同一条视频重新走预处理，已保存的转写不会丢失。

如果音频流始终被拦截，后端会自动退化为下载低清视频再在本地抽取音轨（状态栏显示 `Audio stream blocked, extracting from video...`），转写仍可继续。

## 测试

```bash
python -m pytest tests/ -q
```

测试使用临时 SQLite 数据库和模拟调用，不需要真实 API 密钥。
