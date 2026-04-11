#!/usr/bin/env python3
"""
Unified message poller for TG + WeChat.
Saves messages to /root/inbox/messages.jsonl with status=pending.
Images/files saved to /root/inbox/images/ and /root/inbox/files/.
Reply helper: /root/inbox/reply.py
"""

import asyncio
import base64
import json
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path

import tempfile

import httpx
import edge_tts
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from faster_whisper import WhisperModel
from msg_store import list_pending, clear_all_pending
from menu import handle_menu
from paipai_agent import process_message as paipai_think_lite
from paipai_full import send_and_wait as paipai_think_full

logging.basicConfig(
    format="%(asctime)s [派派] %(message)s",
    level=logging.INFO,
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pulse")

# ======================== Config ========================
INBOX_DIR = "/root/inbox"
MSG_FILE = f"{INBOX_DIR}/messages.jsonl"
IMG_DIR = f"{INBOX_DIR}/images"
FILE_DIR = f"{INBOX_DIR}/files"

# TG
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_OWNER = int(os.environ.get("TG_OWNER", "0"))
TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"

# WeChat
WX_STATE_FILE = os.environ.get("WX_STATE_FILE", "/root/wechat-bot/state.json")

# Claude status
STATUS_FILE = os.environ.get("STATUS_FILE", "/root/inbox/claude_status.json")

os.makedirs(IMG_DIR, exist_ok=True)
os.makedirs(FILE_DIR, exist_ok=True)

# ======================== Voice (STT + TTS) ========================
log.info("🎙️ Loading whisper model...")
_whisper = WhisperModel("medium", device="cpu", compute_type="int8")
log.info("🎙️ Whisper model ready")

VOICE_MAP = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "en": "en-US-AriaNeural",
}
VOICE_DEFAULT = "zh-CN-XiaoxiaoNeural"

# ======================== Message Store ========================

PRIORITY_PREFIXES = {
    "/urgent": "urgent",
    "/btw": "btw",
}

# Dedup: track recent message fingerprints (survives within process lifetime)
_recent_msgs = set()
_DEDUP_MAX = 500

def _msg_fingerprint(msg: dict) -> str:
    """Generate fingerprint from source + key fields to detect duplicates."""
    src = msg.get("source", "")
    if src == "tg":
        return f"tg:{msg.get('msg_id','')}"
    elif src == "wx":
        # WX has no unique msg_id, use text+ts hash
        return f"wx:{msg.get('text','')[:50]}:{msg.get('from_user','')[-8:]}"
    return ""

def save_message(msg: dict):
    """Append message to inbox. Parses /prefix for priority. Dedup."""
    fp = _msg_fingerprint(msg)
    if fp and fp in _recent_msgs:
        log.debug(f"Dedup: skipping {fp}")
        return
    if fp:
        _recent_msgs.add(fp)
        if len(_recent_msgs) > _DEDUP_MAX:
            _recent_msgs.clear()

    msg.setdefault("id", uuid.uuid4().hex[:12])
    msg.setdefault("status", "pending")
    msg.setdefault("ts", time.time())
    msg.setdefault("reply", None)
    msg.setdefault("priority", "normal")

    # Parse priority prefix
    text = msg.get("text", "")
    for prefix, prio in PRIORITY_PREFIXES.items():
        if text.lower().startswith(prefix):
            msg["priority"] = prio
            msg["text"] = text[len(prefix):].strip()
            break

    with open(MSG_FILE, "a") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    icon = {"tg": "✈️", "wx": "💬"}.get(msg["source"], "📨")
    prio_tag = f" ❗{msg['priority']}" if msg["priority"] != "normal" else ""
    content = msg.get("text", "")[:60] or ("🖼️ 图片" if msg.get("image") else "📎 媒体")
    log.info(f"{icon}{prio_tag} {content}")


# ======================== Command Handler ========================

BUILTIN_CMDS = {
    "/status": "systemctl status inbox-poller --no-pager -l; echo '---'; ps aux | grep -E 'claude|node' | grep -v grep; echo '---'; free -h | head -2; df -h / | tail -1",
    "/ps": "ps aux --sort=-%mem | head -15",
    "/logs": "journalctl -u inbox-poller --no-pager -n 30",
    "/ip": "curl -s ifconfig.me",
    "/uptime": "uptime",
    "/disk": "df -h",
    "/mem": "free -h",
}

async def send_tg_reply(client: httpx.AsyncClient, chat_id: int, text: str, reply_to: int = None):
    """Send text reply to TG, auto-chunk if >4096."""
    for i in range(0, len(text), 4096):
        body = {"chat_id": chat_id, "text": text[i:i+4096]}
        if reply_to and i == 0:
            body["reply_to_message_id"] = reply_to
        await client.post(f"{TG_API}/sendMessage", json=body, timeout=15)

async def send_wx_reply(client: httpx.AsyncClient, to: str, text: str, ctx_token: str = None):
    """Send text reply to WeChat."""
    wx_state = json.loads(Path(WX_STATE_FILE).read_text())
    token = wx_state["bot_token"]
    base = wx_state["base_url"].rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "AuthorizationType": "ilink_bot_token",
    }
    for i in range(0, len(text), 4000):
        body = {"msg": {
            "from_user_id": "",
            "to_user_id": to,
            "client_id": f"cmd-{uuid.uuid4().hex[:8]}",
            "message_type": 2, "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text[i:i+4000]}}],
        }}
        if ctx_token:
            body["msg"]["context_token"] = ctx_token
        await client.post(f"{base}/ilink/bot/sendmessage", json=body, headers=headers, timeout=15)

async def _forward_to_hermes(client, text, source, **ctx):
    """Forward message to Hermes Agent via tmux, capture reply."""
    import subprocess as _sp
    # Send to hermes tmux
    escaped = text.replace("'", "'\"'\"'")
    _sp.run(f"tmux send-keys -t hermes '{escaped}' Enter", shell=True, timeout=5, capture_output=True)
    log.info(f"🔮 → hermes: {text[:40]}")

    # Wait for reply (poll tmux pane)
    import asyncio
    await asyncio.sleep(8)
    for _ in range(12):  # max 60s
        pane = _sp.run("tmux capture-pane -t hermes -p -S -30", shell=True, capture_output=True, text=True, timeout=5).stdout
        # Check if hermes is idle (has prompt ❯)
        lines = pane.strip().splitlines()
        if lines and "❯" in lines[-1]:
            # Extract response between the user input and the prompt
            reply_lines = []
            found_input = False
            for line in lines:
                if text[:20] in line:
                    found_input = True
                    continue
                if found_input:
                    s = line.strip()
                    if "❯" in s or s.startswith("⚕") or "─────" in s:
                        continue
                    if s:
                        reply_lines.append(line)
            if reply_lines:
                reply = "\n".join(reply_lines).strip()
                if len(reply) > 3000:
                    reply = reply[:3000] + "\n...(截断)"
                if source == "tg":
                    await send_tg_reply(client, ctx["chat_id"], f"🔮 Hermes:\n{reply}", ctx.get("msg_id"))
                elif source == "wx":
                    await send_wx_reply(client, ctx["from_user"], f"🔮 Hermes:\n{reply}", ctx.get("context_token"))
                log.info(f"🔮 hermes回复 → {source}: {reply[:40]}")
                return
        await asyncio.sleep(5)

    # Timeout
    if source == "tg":
        await send_tg_reply(client, ctx["chat_id"], "🔮 Hermes 思考中，请稍后查看", ctx.get("msg_id"))
    elif source == "wx":
        await send_wx_reply(client, ctx["from_user"], "🔮 Hermes 思考中，请稍后查看", ctx.get("context_token"))

async def handle_command(client: httpx.AsyncClient, text: str, source: str, **ctx) -> bool:
    """Handle /commands and menu interactions. Returns True if handled."""
    # /h suffix — forward to Hermes Agent
    if text.endswith("/h") or text.endswith("/H"):
        msg = text[:-2].strip()
        if msg:
            asyncio.create_task(_forward_to_hermes(client, msg, source, **ctx))
            return True

    # Menu system — handles /menu and active menu sessions
    menu_reply = handle_menu(text, source, **ctx)
    if menu_reply is not None:
        if source == "tg":
            await send_tg_reply(client, ctx["chat_id"], menu_reply, ctx.get("msg_id"))
        elif source == "wx":
            await send_wx_reply(client, ctx["from_user"], menu_reply, ctx.get("context_token"))
        log.info(f"📋 menu → {source}")
        return True

    if not text.startswith("/"):
        return False

    parts = text.split(None, 1)
    cmd = parts[0].lower()

    # /run <bash command>
    if cmd == "/run" and len(parts) > 1:
        bash_cmd = parts[1]
    elif cmd in BUILTIN_CMDS:
        bash_cmd = BUILTIN_CMDS[cmd]
    elif cmd == "/help":
        bash_cmd = None
        help_text = (
            "🤖 派派 Pulse — 消息中枢\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📋 消息管理\n"
            "  /pending    查看待处理消息\n"
            "  /clear      清除所有待处理\n"
            "  /reply ID 内容  远程回复\n"
            "\n"
            "🖥️ 系统运维\n"
            "  /status     服务·进程·内存\n"
            "  /run 命令   执行任意 bash\n"
            "  /ps         进程列表\n"
            "  /logs       最近 30 行日志\n"
            "  /restart 服务  重启服务\n"
            "\n"
            "📊 快捷查询\n"
            "  /mem  /disk  /uptime  /ip\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "💡 消息前缀: /urgent 加急 /btw 低优先"
        )
    elif cmd == "/ai":
        global PAIPAI_MODE
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        if arg in ("off", "关"):
            PAIPAI_MODE = "off"
            help_text = "🤖 派派 AI 已关闭"
        elif arg in ("lite", "轻量"):
            PAIPAI_MODE = "lite"
            help_text = "🤖 派派 AI 轻量模式 (cc-bridge sonnet)"
        elif arg in ("full", "全能"):
            PAIPAI_MODE = "full"
            help_text = "🤖 派派 AI 全能模式 (claude -p)"
        elif arg in ("auto", "自动"):
            PAIPAI_MODE = "auto"
            help_text = "🤖 派派 AI 自动模式\n默认轻量，主会话异常时自动切全能接管"
        else:
            current_effective = _detect_mode() if PAIPAI_MODE == "auto" else PAIPAI_MODE
            help_text = (
                f"🤖 派派 AI: {PAIPAI_MODE}"
                f"{' → 当前' + current_effective if PAIPAI_MODE == 'auto' else ''}\n\n"
                "/ai auto — 自动 (推荐)\n"
                "/ai lite — 轻量 (API)\n"
                "/ai full — 全能 (claude -p)\n"
                "/ai off — 关闭"
            )
        bash_cmd = None
    elif cmd == "/restart" and len(parts) > 1:
        svc = parts[1].strip()
        allowed = {"poller": "inbox-poller", "proxy": "claude-proxy", "bridge": "wechat-bridge"}
        svc_name = allowed.get(svc, svc)
        bash_cmd = f"systemctl restart {svc_name} && systemctl is-active {svc_name}"
    elif cmd == "/pending":
        pending = list_pending()
        if not pending:
            help_text = "✅ 没有待处理消息"
        else:
            import datetime
            lines = [f"📬 待处理 {len(pending)} 条:"]
            for m in pending[:15]:
                icon = {"tg": "✈️", "wx": "💬"}.get(m["source"], "📨")
                ts = datetime.datetime.fromtimestamp(m["ts"]).strftime("%H:%M")
                txt = m.get("text", "")[:40]
                media = " 🖼️" if m.get("image") else (" 📎" if m.get("file") else "")
                lines.append(f"  {icon} {m['id']} {ts}{media} {txt}")
            if len(pending) > 15:
                lines.append(f"  ... 还有 {len(pending)-15} 条")
            help_text = "\n".join(lines)
        bash_cmd = None
    elif cmd == "/clear":
        count = clear_all_pending()
        help_text = f"🧹 已清除 {count} 条待处理消息"
        bash_cmd = None
    elif cmd == "/reply" and len(parts) > 1:
        reply_parts = parts[1].split(None, 1)
        if len(reply_parts) < 2:
            help_text = "用法: /reply <msg_id> <text>"
        else:
            rid, rtext = reply_parts
            try:
                result = subprocess.run(
                    ["python3", "/root/inbox/reply.py", rid, rtext],
                    capture_output=True, text=True, timeout=30,
                    cwd="/root/inbox",
                )
                help_text = result.stdout.strip() or result.stderr.strip() or "已回复"
            except Exception as e:
                help_text = f"回复失败: {e}"
        bash_cmd = None
    else:
        return False  # Not a recognized command, pass through

    if bash_cmd is not None:
        log.info(f"⚡ {source}: {bash_cmd[:60]}")
        try:
            result = subprocess.run(
                bash_cmd, shell=True, capture_output=True, text=True, timeout=30,
            )
            output = (result.stdout + result.stderr).strip() or "(no output)"
        except subprocess.TimeoutExpired:
            output = "⏱️ 超时 (30s)"
        except Exception as e:
            output = f"❌ {e}"
        help_text = f"$ {bash_cmd}\n\n{output}"

    # Reply to source
    if source == "tg":
        await send_tg_reply(client, ctx["chat_id"], help_text, ctx.get("msg_id"))
    elif source == "wx":
        await send_wx_reply(client, ctx["from_user"], help_text, ctx.get("context_token"))

    log.info(f"📤 cmd回复 → {source}")
    return True


# ======================== TG Poller ========================

async def tg_poll():
    offset = 0
    async with httpx.AsyncClient() as client:
        log.info("✈️ TG 通道就绪")
        while True:
            try:
                resp = await client.get(
                    f"{TG_API}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                    timeout=40,
                )
                data = resp.json()
                if not data.get("ok"):
                    await asyncio.sleep(5)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message")
                    if not msg:
                        continue
                    user = msg.get("from", {})
                    uid = user.get("id")
                    if uid != TG_OWNER:
                        continue

                    chat_id = msg["chat"]["id"]
                    msg_id = msg["message_id"]
                    text = msg.get("text", "")
                    caption = msg.get("caption", "")

                    entry = {
                        "source": "tg",
                        "chat_id": chat_id,
                        "msg_id": msg_id,
                        "user": user.get("first_name", ""),
                        "text": text or caption,
                    }

                    # Photo
                    if msg.get("photo"):
                        photo = msg["photo"][-1]
                        file_resp = await client.get(
                            f"{TG_API}/getFile",
                            params={"file_id": photo["file_id"]},
                            timeout=10,
                        )
                        fdata = file_resp.json()
                        if fdata.get("ok"):
                            fpath = fdata["result"]["file_path"]
                            dl = await client.get(
                                f"https://api.telegram.org/file/bot{TG_TOKEN}/{fpath}",
                                timeout=30,
                            )
                            raw_tg = dl.content
                            ext_tg = "jpg" if raw_tg[:2] == b'\xff\xd8' else "png"
                            local = f"{IMG_DIR}/tg_{msg_id}.{ext_tg}"
                            Path(local).write_bytes(raw_tg)
                            entry["image"] = local
                            entry["image_b64"] = base64.b64encode(raw_tg).decode()
                            entry["image_mime"] = "image/jpeg" if ext_tg == "jpg" else "image/png"
                            log.info(f"TG image saved: {local}")

                    # Document
                    if msg.get("document"):
                        doc = msg["document"]
                        file_resp = await client.get(
                            f"{TG_API}/getFile",
                            params={"file_id": doc["file_id"]},
                            timeout=10,
                        )
                        fdata = file_resp.json()
                        if fdata.get("ok"):
                            fpath = fdata["result"]["file_path"]
                            dl = await client.get(
                                f"https://api.telegram.org/file/bot{TG_TOKEN}/{fpath}",
                                timeout=30,
                            )
                            fname = doc.get("file_name", f"file_{msg_id}")
                            local = f"{FILE_DIR}/tg_{fname}"
                            Path(local).write_bytes(dl.content)
                            entry["file"] = local
                            log.info(f"TG file saved: {local}")

                    # Voice
                    if msg.get("voice"):
                        voice = msg["voice"]
                        file_resp = await client.get(
                            f"{TG_API}/getFile",
                            params={"file_id": voice["file_id"]},
                            timeout=10,
                        )
                        fdata = file_resp.json()
                        if fdata.get("ok"):
                            fpath = fdata["result"]["file_path"]
                            dl = await client.get(
                                f"https://api.telegram.org/file/bot{TG_TOKEN}/{fpath}",
                                timeout=30,
                            )
                            local = f"{FILE_DIR}/tg_voice_{msg_id}.ogg"
                            Path(local).write_bytes(dl.content)
                            entry["voice"] = local

                    # Handle /commands directly (don't save to inbox)
                    if (text or caption) and await handle_command(
                        client, text or caption, "tg",
                        chat_id=chat_id, msg_id=msg_id,
                    ):
                        continue

                    if entry.get("voice") and not text and not caption:
                        asyncio.create_task(voice_reply(client, entry))
                    elif text or caption or entry.get("image") or entry.get("file"):
                        save_message(entry)
                        # 派派 AI auto-reply (non-blocking)
                        if text or caption:
                            asyncio.create_task(auto_reply(client, entry))

            except httpx.TimeoutException:
                continue
            except Exception as e:
                log.error(f"TG error: {e}")
                await asyncio.sleep(5)


# ======================== WeChat Poller ========================

async def wx_poll():
    try:
        wx_state = json.loads(Path(WX_STATE_FILE).read_text())
    except Exception as e:
        log.error(f"WX state load failed: {e}, skipping WeChat")
        return

    token = wx_state.get("bot_token")
    base_url = wx_state.get("base_url", "https://ilinkai.weixin.qq.com")
    buf = wx_state.get("get_updates_buf", "")
    owner = wx_state.get("owner_user_id", "")

    if not token:
        log.warning("No WX bot_token, skipping WeChat")
        return

    fails = 0
    async with httpx.AsyncClient() as client:
        log.info("💬 WX 通道就绪")
        while True:
            try:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                    "AuthorizationType": "ilink_bot_token",
                }
                body = {
                    "get_updates_buf": buf,
                    "base_info": {"channel_version": "poller-2.0"},
                }
                resp = await client.post(
                    f"{base_url}/ilink/bot/getupdates",
                    json=body, headers=headers, timeout=40,
                )
                data = resp.json()

                ret = data.get("ret", 0)
                errcode = data.get("errcode", 0)
                if ret != 0 or errcode != 0:
                    fails += 1
                    if errcode == -14:
                        log.error("WX session expired!")
                        return
                    if fails >= 3:
                        await asyncio.sleep(30)
                        fails = 0
                    else:
                        await asyncio.sleep(2)
                    continue

                fails = 0
                new_buf = data.get("get_updates_buf", "")
                if new_buf:
                    buf = new_buf
                    wx_state["get_updates_buf"] = new_buf
                    Path(WX_STATE_FILE).write_text(
                        json.dumps(wx_state, ensure_ascii=False, indent=2)
                    )

                for msg in data.get("msgs", []):
                    from_user = msg.get("from_user_id", "")
                    ctx_token = msg.get("context_token", "")

                    text = ""
                    image_url = ""
                    image_aeskey = ""
                    for item in msg.get("item_list", []):
                        t = item.get("type")
                        if t == 1:
                            text = item.get("text_item", {}).get("text", "")
                        elif t == 2:
                            img_item = item.get("image_item", {})
                            media = img_item.get("media", {})
                            image_url = media.get("full_url", "")
                            image_aeskey = img_item.get("aeskey", "")
                        elif t == 3:
                            vt = item.get("voice_item", {}).get("text", "")
                            if vt and not text:
                                text = vt
                        else:
                            # Log unknown types (video=4, file=5, etc.)
                            log.info(f"[wx] unknown item type={t}: {json.dumps(item, ensure_ascii=False, default=str)[:500]}")

                    entry = {
                        "source": "wx",
                        "from_user": from_user,
                        "context_token": ctx_token,
                        "text": text,
                    }

                    # Download image if present
                    if image_url:
                        try:
                            img_headers = dict(headers)
                            img_resp = await client.get(
                                image_url, headers=img_headers,
                                timeout=30, follow_redirects=True,
                            )
                            raw = img_resp.content
                            # Decrypt AES-ECB if aeskey provided
                            if image_aeskey:
                                try:
                                    aes_key = bytes.fromhex(image_aeskey)
                                    cipher = Cipher(algorithms.AES(aes_key), modes.ECB())
                                    d = cipher.decryptor()
                                    raw = d.update(raw) + d.finalize()
                                    # Strip PKCS7 padding
                                    pad = raw[-1]
                                    if 1 <= pad <= 16 and all(b == pad for b in raw[-pad:]):
                                        raw = raw[:-pad]
                                except Exception as e:
                                    log.warning(f"AES decrypt failed, saving raw: {e}")
                            # Save to disk (claude -p needs file path)
                            ext = "jpg" if raw[:2] == b'\xff\xd8' else "png"
                            local = f"{IMG_DIR}/wx_{uuid.uuid4().hex[:8]}.{ext}"
                            Path(local).write_bytes(raw)
                            # Also store base64 for direct API use
                            entry["image"] = local
                            entry["image_b64"] = base64.b64encode(raw).decode()
                            entry["image_mime"] = "image/jpeg" if ext == "jpg" else "image/png"
                            log.info(f"WX image saved: {local} ({len(raw)} bytes)")
                        except Exception as e:
                            log.error(f"WX image download failed: {e}")

                    # Handle /commands directly
                    if text and await handle_command(
                        client, text, "wx",
                        from_user=from_user, context_token=ctx_token,
                    ):
                        continue

                    if text or entry.get("image"):
                        save_message(entry)
                        # 派派 AI auto-reply (non-blocking)
                        if text:
                            asyncio.create_task(auto_reply(client, entry))

            except httpx.TimeoutException:
                continue
            except Exception as e:
                log.error(f"WX error: {e}")
                fails += 1
                if fails >= 3:
                    await asyncio.sleep(30)
                    fails = 0
                else:
                    await asyncio.sleep(2)


# ======================== Webhook Server ========================

from aiohttp import web

WEBHOOK_PORT = 8900
WEBHOOK_TOKEN = os.environ.get("WEBHOOK_TOKEN", "pulse-secret")

async def webhook_handler(request: web.Request):
    """POST /api/message — receive message and save to inbox.
    Body: {"text": "...", "source": "api", "token": "..."}
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    if data.get("token") != WEBHOOK_TOKEN:
        return web.json_response({"error": "unauthorized"}, status=401)

    text = data.get("text", "").strip()
    if not text:
        return web.json_response({"error": "empty text"}, status=400)

    msg = {
        "source": data.get("source", "api"),
        "text": text,
        "from_user": data.get("from_user", "webhook"),
    }
    save_message(msg)
    log.info(f"🌐 webhook: {text[:40]}")
    return web.json_response({"ok": True, "id": msg["id"]})


async def webhook_command(request: web.Request):
    """POST /api/command — execute a command and return result.
    Body: {"command": "/status", "token": "..."}
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    if data.get("token") != WEBHOOK_TOKEN:
        return web.json_response({"error": "unauthorized"}, status=401)

    cmd_text = data.get("command", "").strip()
    if not cmd_text:
        return web.json_response({"error": "empty command"}, status=400)

    # Reuse handle_command logic but capture output
    parts = cmd_text.split(None, 1)
    cmd = parts[0].lower()

    if cmd == "/run" and len(parts) > 1:
        bash_cmd = parts[1]
    elif cmd in BUILTIN_CMDS:
        bash_cmd = BUILTIN_CMDS[cmd]
    else:
        return web.json_response({"error": f"unknown command: {cmd}"}, status=400)

    try:
        result = subprocess.run(
            bash_cmd, shell=True, capture_output=True, text=True, timeout=30,
        )
        output = (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        output = "timeout"
    except Exception as e:
        output = str(e)

    return web.json_response({"ok": True, "output": output})


async def webhook_pending(request: web.Request):
    """GET /api/pending — list pending messages."""
    token = request.query.get("token", "")
    if token != WEBHOOK_TOKEN:
        return web.json_response({"error": "unauthorized"}, status=401)

    pending = list_pending()
    items = []
    for m in pending[:50]:
        items.append({
            "id": m["id"],
            "source": m["source"],
            "text": m.get("text", ""),
            "ts": m.get("ts", 0),
            "image": bool(m.get("image")),
        })
    return web.json_response({"ok": True, "count": len(pending), "messages": items})


async def webhook_health(request: web.Request):
    """GET /api/health — health check."""
    return web.json_response({
        "service": "派派 Pulse",
        "status": "running",
        "ts": time.time(),
    })


async def start_webhook():
    """Start webhook HTTP server."""
    app = web.Application()
    app.router.add_get("/api/health", webhook_health)
    app.router.add_get("/api/pending", webhook_pending)
    app.router.add_post("/api/message", webhook_handler)
    app.router.add_post("/api/command", webhook_command)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    log.info(f"🌐 Webhook 就绪 | http://0.0.0.0:{WEBHOOK_PORT}/api/")


# ======================== Auto Reply (派派 AI) ========================

# Toggle: "off" / "auto" (smart) / "lite" (API only) / "full" (claude -p only)
PAIPAI_MODE = "auto"

def _detect_mode():
    """Auto mode: lite normally, full when main session is down."""
    if PAIPAI_MODE == "auto":
        try:
            cs = json.loads(Path(STATUS_FILE).read_text())
            if cs.get("state") in ("offline", "error"):
                return "full"
        except Exception:
            return "full"  # can't read status = assume down
        return "lite"
    return PAIPAI_MODE

async def auto_reply(client: httpx.AsyncClient, entry: dict):
    """派派 AI 自动回复。出错时静默降级，不打扰用户。"""
    mode = _detect_mode()
    if mode == "off":
        return
    source = entry["source"]
    text = entry.get("text", "")
    if not text:
        return

    # 1. 即时确认（简洁）
    cs = {}
    try:
        cs = json.loads(Path(STATUS_FILE).read_text())
    except Exception:
        pass
    status_icon = {"idle": "🟢", "busy": "🔴", "thinking": "🟡", "offline": "⚫"}.get(cs.get("state", ""), "⚪")
    ack = f"📨 收到 | Claude {status_icon}{cs.get('label', '未知')}"

    if source == "tg":
        await send_tg_reply(client, entry["chat_id"], ack, entry.get("msg_id"))
    elif source == "wx":
        await send_wx_reply(client, entry["from_user"], ack, entry.get("context_token"))

    # 2. 派派 AI 思考
    try:
        reply = await paipai_think_full(text)
    except Exception as e:
        log.error(f"派派AI错误: {e}")
        # 静默降级 — 不发错误给用户，消息已存 inbox 等主会话处理
        return

    # 检查是否真的有内容（排除错误回复）
    if not reply or "未返回内容" in reply or "出错" in reply or "失败" in reply:
        log.warning(f"派派AI无效回复，静默降级: {reply[:40]}")
        return

    # 3. 推送回复 + 跨平台广播
    if source == "tg":
        await send_tg_reply(client, entry["chat_id"], f"🤖 {reply}", entry.get("msg_id"))
        wx_state = json.loads(Path(WX_STATE_FILE).read_text())
        wx_owner = wx_state.get("owner_user_id", "")
        if wx_owner:
            await send_wx_reply(client, wx_owner, f"[TG→WX] 🤖 {reply}")
    elif source == "wx":
        await send_wx_reply(client, entry["from_user"], f"🤖 {reply}", entry.get("context_token"))
        await send_tg_reply(client, TG_OWNER, f"[WX→TG] 🤖 {reply}")

    log.info(f"🤖 派派回复 → {source}: {reply[:40]}")


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


# ======================== Main ========================

async def main():
    log.info(f"🚀 派派启动 | 消息 → {MSG_FILE}")
    await asyncio.gather(tg_poll(), wx_poll(), start_webhook())

if __name__ == "__main__":
    asyncio.run(main())
