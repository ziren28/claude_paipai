# Voice Reply Design — 派派语音对话功能

**Date:** 2026-04-11  
**Status:** Approved  
**Scope:** TG 语音消息双向通道（语音输入 → Claude → 语音输出）

---

## 1. 目标

用户通过 Telegram 发送语音消息，派派自动：
1. 转写为文字（STT）
2. 调用 Claude 生成回复
3. 将回复合成语音（TTS）
4. 以 TG 语音消息形式发回

仅支持 TG 通道，微信暂不扩展。回复只发语音，不附加文字。

---

## 2. 数据流

```
用户发 TG 语音消息
    ↓
tg_poll() 下载 .ogg → /root/inbox/files/tg_voice_{msg_id}.ogg  [现有]
    ↓ asyncio.create_task(voice_reply(client, entry))            [新增]
voice_reply():
    ├─ faster-whisper medium 转写 → transcript + detected_language
    ├─ paipai_think_full(transcript) → reply_text               [复用现有]
    ├─ edge-tts (按 language 选音色) → /tmp/vr_{uuid}.mp3
    ├─ ffmpeg -i .mp3 -c:a libopus .ogg                         [转码]
    └─ TG sendVoice API → 用户收到语音回复
```

语音消息**不写入** `messages.jsonl`，不进文字 inbox，纯实时处理。

---

## 3. 组件设计

### 3.1 STT — faster-whisper medium

- 包：`faster-whisper`
- 模型：`medium`，`device="cpu"`，`compute_type="int8"`
- 内存：约 500MB
- 推理时间：约 10-15 秒/条（CPU）
- 语言检测：自动，返回 `language` 字段（`zh` / `en` 等）
- 加载时机：`poller.py` 启动时全局单例，避免重复加载

```python
from faster_whisper import WhisperModel
_whisper = WhisperModel("medium", device="cpu", compute_type="int8")
```

### 3.2 TTS — edge-tts

- 包：`edge-tts`
- 音色映射：

| 检测语言 | 音色 |
|---------|------|
| `zh` | `zh-CN-XiaoxiaoNeural`（微软小晓） |
| `en` | `en-US-AriaNeural` |
| 其他 | 默认 `zh-CN-XiaoxiaoNeural` |

- 输出：`/tmp/vr_{uuid}.mp3`

### 3.3 音频转码 — ffmpeg

TG `sendVoice` 要求 OGG/OPUS 格式：

```bash
ffmpeg -i /tmp/vr_{uuid}.mp3 -c:a libopus /tmp/vr_{uuid}.ogg
```

临时文件在 `sendVoice` 成功后立即删除。

### 3.4 TG sendVoice

```
POST /bot{TOKEN}/sendVoice
{
  "chat_id": ...,
  "voice": <binary>,
  "reply_to_message_id": original_msg_id
}
```

---

## 4. 错误处理

| 阶段 | 失败处理 |
|------|---------|
| STT 转写失败 | TG 发文字：「⚠️ 语音识别失败，请重发」|
| Claude 出错 | 静默降级（同现有 auto_reply 策略） |
| TTS 生成失败 | 降级：TG 发文字回复 |
| sendVoice 失败 | 降级：TG 发文字回复 |

---

## 5. 改动范围

**仅改动 `/root/inbox/poller.py`，其他文件不动。**

| 位置 | 类型 | 描述 |
|------|------|------|
| 顶部 imports | 新增 | `faster_whisper`, `edge_tts`, `tempfile`, `subprocess` |
| 全局初始化区 | 新增 | `_whisper = WhisperModel(...)` |
| 新函数 | 新增 | `async def voice_reply(client, entry)` ~50 行 |
| `tg_poll()` ~L454 | 新增 1 行 | `if entry.get("voice"): create_task(voice_reply(...))` |

**不改动：** `stream_reply.py`、`reply.py`、`msg_store.py`、systemd service 文件。

---

## 6. 依赖安装

```bash
pip install faster-whisper edge-tts
```

ffmpeg 已预装（`/usr/bin/ffmpeg`）。

---

## 7. 部署

```bash
systemctl restart inbox-poller
```

首次启动时 faster-whisper 会自动下载 medium 模型（~1.5GB），需要等待约 1-2 分钟。

---

## 8. 不在范围内

- 微信语音支持
- 语音 + 文字双发
- 语音命令（如 `/status`）
- 多语言扩展（目前仅中/英）
