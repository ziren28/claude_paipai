# Voice Reply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable TG voice message → STT → Claude → TTS → TG voice reply in poller.py.

**Architecture:** Extend `poller.py` with a new `voice_reply()` coroutine. On receiving a TG voice message, transcribe with faster-whisper, generate Claude reply via existing `paipai_think_full()`, synthesize audio with edge-tts, convert to OGG/OPUS via ffmpeg, send back via TG `sendVoice` API.

**Tech Stack:** faster-whisper (medium, int8, CPU), edge-tts, ffmpeg, httpx

**Spec:** `docs/superpowers/specs/2026-04-11-voice-reply-design.md`

---

### Task 1: Install Dependencies

**Files:** None (system packages only)

- [ ] **Step 1: Install Python packages**

```bash
pip install faster-whisper edge-tts
```

Expected: Both install successfully. `faster-whisper` pulls CTranslate2 + huggingface_hub.

- [ ] **Step 2: Verify imports work**

```bash
python3 -c "from faster_whisper import WhisperModel; print('whisper ok')"
python3 -c "import edge_tts; print('tts ok')"
```

Expected: Both print "ok".

- [ ] **Step 3: Pre-download whisper medium model**

```bash
python3 -c "
from faster_whisper import WhisperModel
print('Downloading medium model...')
m = WhisperModel('medium', device='cpu', compute_type='int8')
print('Model ready')
"
```

Expected: Downloads ~1.5GB model to `~/.cache/huggingface/`. Takes 1-2 minutes.

---

### Task 2: Add voice_reply() to poller.py

**Files:**
- Modify: `/root/inbox/poller.py`

- [ ] **Step 1: Add imports at top of poller.py**

After the existing imports (line 20, after `from cryptography...`), add:

```python
import tempfile
from faster_whisper import WhisperModel
import edge_tts
```

- [ ] **Step 2: Add whisper model global initialization**

After the `os.makedirs` lines (after line 51), add:

```python
# ======================== Voice (STT + TTS) ========================
log.info("🎙️ Loading whisper model...")
_whisper = WhisperModel("medium", device="cpu", compute_type="int8")
log.info("🎙️ Whisper model ready")

VOICE_MAP = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "en": "en-US-AriaNeural",
}
VOICE_DEFAULT = "zh-CN-XiaoxiaoNeural"
```

- [ ] **Step 3: Add voice_reply() function**

Add before the `# ======================== Main ========================` section (before line 802):

```python
async def voice_reply(client: httpx.AsyncClient, entry: dict):
    """TG voice → STT → Claude → TTS → TG voice reply."""
    ogg_path = entry.get("voice")
    chat_id = entry["chat_id"]
    reply_to = entry.get("msg_id")

    if not ogg_path or not Path(ogg_path).exists():
        return

    # 1. STT — transcribe voice
    try:
        segments, info = _whisper.transcribe(ogg_path)
        transcript = "".join(s.text for s in segments).strip()
        lang = info.language or "zh"
        log.info(f"🎙️ STT [{lang}]: {transcript[:60]}")
    except Exception as e:
        log.error(f"🎙️ STT failed: {e}")
        await send_tg_reply(client, chat_id, "⚠️ 语音识别失败，请重发", reply_to)
        return

    if not transcript:
        await send_tg_reply(client, chat_id, "⚠️ 未识别到语音内容", reply_to)
        return

    # 2. Claude reply
    try:
        reply_text = await paipai_think_full(transcript)
    except Exception as e:
        log.error(f"🎙️ Claude error: {e}")
        return

    if not reply_text or "未返回内容" in reply_text or "出错" in reply_text:
        log.warning(f"🎙️ Claude无效回复，静默降级: {(reply_text or '')[:40]}")
        return

    log.info(f"🎙️ Reply: {reply_text[:60]}")

    # 3. TTS — synthesize voice
    voice = VOICE_MAP.get(lang, VOICE_DEFAULT)
    tmp_mp3 = tempfile.mktemp(suffix=".mp3", prefix="vr_")
    tmp_ogg = tempfile.mktemp(suffix=".ogg", prefix="vr_")
    try:
        tts = edge_tts.Communicate(reply_text, voice)
        await tts.save(tmp_mp3)

        # Convert to OGG/OPUS for TG sendVoice
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", tmp_mp3, "-c:a", "libopus", tmp_ogg,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if proc.returncode != 0 or not Path(tmp_ogg).exists():
            raise RuntimeError("ffmpeg conversion failed")

    except Exception as e:
        log.error(f"🎙️ TTS failed: {e}, falling back to text")
        await send_tg_reply(client, chat_id, reply_text, reply_to)
        return
    finally:
        Path(tmp_mp3).unlink(missing_ok=True)

    # 4. Send voice via TG
    try:
        with open(tmp_ogg, "rb") as f:
            resp = await client.post(
                f"{TG_API}/sendVoice",
                data={"chat_id": chat_id, "reply_to_message_id": reply_to},
                files={"voice": ("reply.ogg", f, "audio/ogg")},
                timeout=30,
            )
        result = resp.json()
        if not result.get("ok"):
            raise RuntimeError(f"sendVoice failed: {result}")
        log.info(f"🎙️ Voice sent → tg")
    except Exception as e:
        log.error(f"🎙️ sendVoice failed: {e}, falling back to text")
        await send_tg_reply(client, chat_id, reply_text, reply_to)
    finally:
        Path(tmp_ogg).unlink(missing_ok=True)
```

- [ ] **Step 4: Wire voice_reply into tg_poll()**

In `tg_poll()`, after the existing save_message block (around line 454-458), add voice handling:

Change:
```python
                    if text or caption or entry.get("image") or entry.get("file"):
                        save_message(entry)
                        # 派派 AI auto-reply (non-blocking)
                        if text or caption:
                            asyncio.create_task(auto_reply(client, entry))
```

To:
```python
                    if entry.get("voice") and not text and not caption:
                        asyncio.create_task(voice_reply(client, entry))
                    elif text or caption or entry.get("image") or entry.get("file"):
                        save_message(entry)
                        # 派派 AI auto-reply (non-blocking)
                        if text or caption:
                            asyncio.create_task(auto_reply(client, entry))
```

- [ ] **Step 5: Commit**

```bash
cd /root/inbox && git add poller.py && git commit -m "feat: add TG voice reply — STT(whisper) + TTS(edge-tts)"
```

---

### Task 3: Test End-to-End

**Files:** None (manual testing)

- [ ] **Step 1: Restart poller**

```bash
systemctl restart inbox-poller
```

Wait 30-60 seconds for whisper model to load. Check logs:

```bash
journalctl -u inbox-poller --no-pager -n 20
```

Expected: See "🎙️ Loading whisper model..." then "🎙️ Whisper model ready".

- [ ] **Step 2: Send Chinese voice from TG**

Send a voice message in Chinese to @max28_ai_bot via Telegram. Say something like "你好，今天天气怎么样"

Watch logs:
```bash
tail -f /root/inbox/poller.log | grep "🎙️"
```

Expected:
1. `🎙️ STT [zh]: 你好今天天气怎么样`
2. `🎙️ Reply: ...`
3. `🎙️ Voice sent → tg`
4. Receive a voice reply in TG with XiaoxiaoNeural voice

- [ ] **Step 3: Send English voice from TG**

Send a voice message in English. Say "Hello, what's the weather today?"

Expected: Same flow, with `[en]` language detection and AriaNeural voice.

- [ ] **Step 4: Test error cases**

1. Send a very short voice (< 1 second) — should either transcribe or show "未识别到语音内容"
2. Check that text messages still work normally after the change
3. Check that WeChat polling is unaffected

- [ ] **Step 5: Verify memory usage**

```bash
ps aux | grep poller | grep -v grep
free -h
```

Expected: poller RSS around 600-800MB (whisper model loaded). System still has >3GB free.
