#!/usr/bin/env python3
"""
Stream reply — launches claude -p with stream-json output,
pushes real-time typewriter effect to TG (editMessage) and WeChat (chunked send).

Usage:
  python3 stream_reply.py <msg_id>              # auto-reply with claude
  python3 stream_reply.py <msg_id> "custom prompt"  # override prompt
"""

import asyncio
import json
import sys
import os
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, "/root/inbox")
from msg_store import find_message, mark_replied

# ======================== Config ========================
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"
TG_OWNER_CHAT = int(os.environ.get("TG_OWNER", "0"))

WX_STATE_FILE = os.environ.get("WX_STATE_FILE", "/root/wechat-bot/state.json")
WX_STATE = json.loads(Path(WX_STATE_FILE).read_text()) if Path(WX_STATE_FILE).exists() else {}
WX_OWNER = WX_STATE.get("owner_user_id", "")

CLAUDE_CMD = "claude"
WORK_DIR = "/root"

# Streaming intervals
TG_EDIT_INTERVAL = 1.0    # edit TG message every 1s
WX_CHUNK_INTERVAL = 3.0   # send WX chunk every 3s
WX_CHUNK_MIN_CHARS = 20   # min new chars before sending WX update


# ======================== TG Streaming ========================

class TGStreamer:
    def __init__(self, client: httpx.AsyncClient, chat_id: int, reply_to: int = None):
        self.client = client
        self.chat_id = chat_id
        self.reply_to = reply_to
        self.message_id = None
        self.last_text = ""
        self.last_edit = 0

    async def start(self, initial="⏳ 思考中..."):
        body = {"chat_id": self.chat_id, "text": initial}
        if self.reply_to:
            body["reply_to_message_id"] = self.reply_to
        resp = await self.client.post(f"{TG_API}/sendMessage", json=body, timeout=10)
        data = resp.json()
        if data.get("ok"):
            self.message_id = data["result"]["message_id"]

    async def update(self, text: str, force=False):
        if not self.message_id or text == self.last_text:
            return
        now = time.time()
        if not force and (now - self.last_edit) < TG_EDIT_INTERVAL:
            return
        # TG has 4096 char limit
        display = text[:4000] + "..." if len(text) > 4000 else text
        try:
            await self.client.post(f"{TG_API}/editMessageText", json={
                "chat_id": self.chat_id,
                "message_id": self.message_id,
                "text": display or "...",
            }, timeout=10)
            self.last_text = text
            self.last_edit = now
        except:
            pass

    async def finish(self, text: str):
        """Final update — send full text with Markdown, split if needed."""
        if not self.message_id:
            return
        # Try Markdown first, fallback to plain text
        if len(text) <= 4096:
            try:
                await self.client.post(f"{TG_API}/editMessageText", json={
                    "chat_id": self.chat_id,
                    "message_id": self.message_id,
                    "text": text or "...",
                    "parse_mode": "Markdown",
                }, timeout=10)
            except Exception:
                await self.update(text, force=True)  # fallback plain
        else:
            # Delete the streaming message, send full in chunks
            try:
                await self.client.post(f"{TG_API}/deleteMessage", json={
                    "chat_id": self.chat_id,
                    "message_id": self.message_id,
                }, timeout=5)
            except Exception:
                pass
            for i in range(0, len(text), 4000):
                chunk = text[i:i+4000]
                resp = await self.client.post(f"{TG_API}/sendMessage", json={
                    "chat_id": self.chat_id,
                    "text": chunk,
                    "parse_mode": "Markdown",
                }, timeout=15)
                # Fallback to plain if Markdown fails
                if not resp.json().get("ok"):
                    await self.client.post(f"{TG_API}/sendMessage", json={
                        "chat_id": self.chat_id,
                        "text": chunk,
                    }, timeout=15)


# ======================== WX Streaming ========================

class WXStreamer:
    def __init__(self, client: httpx.AsyncClient, to_user: str, ctx_token: str = None):
        self.client = client
        self.to_user = to_user
        self.ctx_token = ctx_token
        self.sent_len = 0
        self.last_send = 0
        self.chunk_count = 0

    async def update(self, text: str, force=False):
        now = time.time()
        new_text = text[self.sent_len:]
        if not force and (
            (now - self.last_send) < WX_CHUNK_INTERVAL
            or len(new_text) < WX_CHUNK_MIN_CHARS
        ):
            return
        if not new_text.strip():
            return
        # Send chunk with indicator
        chunk = f"[{'▌' if not force else '✓'}] {new_text}"
        await self._send(chunk)
        self.sent_len = len(text)
        self.last_send = now
        self.chunk_count += 1

    async def finish(self, text: str):
        """Send final complete message."""
        # If we sent chunks, send a final complete version
        if self.chunk_count > 0:
            await self._send(f"[完成]\n{text}")
        else:
            await self._send(text)

    async def _send(self, text: str):
        token = WX_STATE["bot_token"]
        base = WX_STATE["base_url"].rstrip("/")
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            body = {"msg": {
                "from_user_id": "",
                "to_user_id": self.to_user,
                "client_id": f"stream-{uuid.uuid4().hex[:12]}",
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": chunk}}],
            }}
            if self.ctx_token:
                body["msg"]["context_token"] = self.ctx_token
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "AuthorizationType": "ilink_bot_token",
            }
            try:
                await self.client.post(
                    f"{base}/ilink/bot/sendmessage",
                    json=body, headers=headers, timeout=15,
                )
            except Exception as e:
                print(f"WX send error: {e}", flush=True)


# ======================== Claude Streaming ========================

async def stream_claude(prompt: str, streamer, session_id: str = None, model: str = "sonnet"):
    """Run claude -p with stream-json, push updates to streamer."""
    cmd = [
        CLAUDE_CMD, "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--model", model,
    ]
    if session_id:
        cmd.extend(["-r", session_id])
    cmd.append(prompt)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=WORK_DIR,
    )

    full_text = ""
    tool_lines = []
    new_session_id = session_id

    # Read line by line for real-time streaming
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        line = line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = event.get("type", "")

        if etype == "system" and "session_id" in event:
            new_session_id = event["session_id"]

        elif etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    full_text += block["text"]
                    await streamer.update(full_text)

        elif etype == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                full_text += delta.get("text", "")
                await streamer.update(full_text)

        elif etype == "tool_use":
            name = event.get("name", "?")
            inp = event.get("input", {})
            if name == "Bash":
                tool_lines.append(f"$ {inp.get('command','?')[:80]}")
            elif name in ("Read", "Edit", "Write"):
                tool_lines.append(f"🔧 {name}: {inp.get('file_path','?')}")
            else:
                tool_lines.append(f"🔧 {name}")
            # Show tool activity
            display = full_text + "\n\n⚙️ " + tool_lines[-1]
            await streamer.update(display, force=True)

        elif etype == "result":
            for block in event.get("content", []):
                if block.get("type") == "text":
                    full_text = block["text"]  # result overrides
            if "session_id" in event:
                new_session_id = event["session_id"]

    await proc.wait()

    # Build final text
    parts = []
    if tool_lines:
        parts.append("⚙️ " + "\n".join(tool_lines))
    if full_text:
        parts.append(full_text)
    final = "\n\n".join(parts) if parts else "(无输出)"

    return final, new_session_id


# ======================== Cross-Platform Broadcast ========================

async def broadcast_final(client: httpx.AsyncClient, msg: dict, text: str):
    """Send final reply to the OTHER platform (cross-sync)."""
    source = msg["source"]
    # Truncate for broadcast to avoid spam
    btext = text[:3000] + "..." if len(text) > 3000 else text

    # Add context about original message (image, file, etc.)
    ctx_parts = []
    if msg.get("image"):
        ctx_parts.append("📷 图片分析")
    if msg.get("file"):
        ctx_parts.append("📎 文件分析")
    orig_text = msg.get("text", "")
    if orig_text:
        ctx_parts.append(f"Q: {orig_text[:100]}")
    ctx_line = " | ".join(ctx_parts)

    if source == "tg" and WX_OWNER:
        prefix = f"[TG→WX] {ctx_line}\n" if ctx_line else "[TG→WX]\n"
        await _broadcast_wx(client, WX_OWNER, prefix + btext)
    elif source == "wx":
        prefix = f"[WX→TG] {ctx_line}\n" if ctx_line else "[WX→TG]\n"
        await _broadcast_tg(client, TG_OWNER_CHAT, prefix + btext)


async def _broadcast_tg(client, chat_id, text):
    for i in range(0, len(text), 4096):
        await client.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id, "text": text[i:i+4096],
        }, timeout=15)


async def _broadcast_wx(client, to_user, text):
    token = WX_STATE["bot_token"]
    base = WX_STATE["base_url"].rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "AuthorizationType": "ilink_bot_token",
    }
    for i in range(0, len(text), 4000):
        body = {"msg": {
            "from_user_id": "", "to_user_id": to_user,
            "client_id": f"bc-{uuid.uuid4().hex[:8]}",
            "message_type": 2, "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text[i:i+4000]}}],
        }}
        await client.post(f"{base}/ilink/bot/sendmessage", json=body, headers=headers, timeout=15)


# ======================== Main ========================

async def main():
    if len(sys.argv) < 2:
        print("Usage: stream_reply.py <msg_id> [custom_prompt]")
        return

    msg_id = sys.argv[1]
    custom_prompt = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else None

    msg = find_message(msg_id)
    if not msg:
        print(f"Message {msg_id} not found")
        return

    # Build prompt
    prompt = custom_prompt or msg.get("text", "")
    if msg.get("image"):
        img_path = msg["image"]
        caption = prompt or "请分析这张图片"
        prompt = f"请查看图片文件 {img_path} ，然后：{caption}"
    if msg.get("file"):
        file_path = msg["file"]
        caption = prompt or "请分析这个文件"
        prompt = f"请查看文件 {file_path} ，然后：{caption}"

    print(f"Processing [{msg['source']}] {msg_id}: {prompt[:60]}")

    async with httpx.AsyncClient() as client:
        # Create streamer based on source
        if msg["source"] == "tg":
            streamer = TGStreamer(client, msg["chat_id"], msg.get("msg_id"))
            await streamer.start()
        elif msg["source"] == "wx":
            streamer = WXStreamer(client, msg["from_user"], msg.get("context_token"))
        else:
            print(f"Unknown source: {msg['source']}")
            return

        # Stream Claude response
        final_text, session_id = await stream_claude(prompt, streamer)

        # Send final message to source platform
        await streamer.finish(final_text)

        # Broadcast to other platform
        try:
            await broadcast_final(client, msg, final_text)
        except Exception as e:
            print(f"Broadcast error: {e}", flush=True)

        # Mark as replied
        mark_replied(msg_id, final_text)
        print(f"Done. Session: {session_id}")


if __name__ == "__main__":
    asyncio.run(main())
