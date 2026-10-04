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

## 测试

```bash
python -m pytest tests/ -q
```

测试使用临时 SQLite 数据库和模拟调用，不需要真实 API 密钥。

## 项目结构

```text
backend/     API、转写、章节、检索与问答
extension/   B 站浏览器扩展
frontend/    本地网页界面
tests/       核心功能测试
```
