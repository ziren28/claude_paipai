#!/usr/bin/env python3
"""
Reply to pending messages.
Usage:
  python3 reply.py <msg_id> "reply text"
  python3 reply.py --list          # show pending messages
  python3 reply.py --mark <msg_id> # mark as replied without sending
"""

import json
import sys
import os
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, "/root/inbox")
from msg_store import load_messages, find_message, mark_replied as store_mark_replied, list_pending as store_list_pending

# TG
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"
TG_OWNER_CHAT = int(os.environ.get("TG_OWNER", "0"))

# WX
WX_STATE_FILE = os.environ.get("WX_STATE_FILE", "/root/paipai/wechat/state.json")
WX_STATE = json.loads(Path(WX_STATE_FILE).read_text()) if Path(WX_STATE_FILE).exists() else {}
WX_OWNER = WX_STATE.get("owner_user_id", "")


def list_pending():
    pending = store_list_pending()
    if not pending:
        print("No pending messages.")
        return
    import datetime
    for m in pending:
        src = m["source"].upper()
        mid = m["id"]
        text = m.get("text", "")[:80]
        img = " [IMG]" if m.get("image") else ""
        f = " [FILE]" if m.get("file") else ""
        prio = m.get("priority", "normal")
        prio_tag = f" !{prio.upper()}" if prio != "normal" else ""
        ts = datetime.datetime.fromtimestamp(m["ts"]).strftime("%H:%M:%S")
        print(f"  [{src}]{prio_tag} {mid} ({ts}){img}{f}: {text}")


def split_paragraphs(text, max_len=300):
    """Split text into natural chunks by paragraphs/sentences."""
    parts = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_len:
            parts.append(para)
        else:
            # Split long paragraphs by sentences
            buf = ""
            for sent in para.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n").replace(". ", ".\n").split("\n"):
                sent = sent.strip()
                if not sent:
                    continue
                if buf and len(buf) + len(sent) > max_len:
                    parts.append(buf)
                    buf = sent
                else:
                    buf = (buf + " " + sent).strip() if buf else sent
            if buf:
                parts.append(buf)
    return parts if parts else [text]


def send_tg(chat_id, text, reply_to=None):
    import time as _time
    with httpx.Client() as c:
        parts = split_paragraphs(text)
        for i, part in enumerate(parts):
            body = {"chat_id": chat_id, "text": part}
            if reply_to and i == 0:
                body["reply_to_message_id"] = reply_to
            r = c.post(f"{TG_API}/sendMessage", json=body, timeout=15)
            print(f"TG sent ({i+1}/{len(parts)}): {r.status_code}")
            if i < len(parts) - 1:
                # Typing delay proportional to length
                delay = min(max(len(part) * 0.01, 0.3), 1.5)
                _time.sleep(delay)


def send_wx(to, text, ctx_token=None):
    import time as _time
    token = WX_STATE["bot_token"]
    base = WX_STATE["base_url"].rstrip("/")
    with httpx.Client() as c:
        parts = split_paragraphs(text)
        for i, chunk in enumerate(parts):
            body = {"msg": {
                "from_user_id": "",
                "to_user_id": to,
                "client_id": f"reply-{uuid.uuid4().hex[:12]}",
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": chunk}}],
            }}
            if ctx_token:
                body["msg"]["context_token"] = ctx_token
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "AuthorizationType": "ilink_bot_token",
            }
            r = c.post(f"{base}/ilink/bot/sendmessage", json=body, headers=headers, timeout=15)
            print(f"WX sent ({i+1}/{len(parts)}): {r.status_code}")
            if i < len(parts) - 1:
                delay = min(max(len(chunk) * 0.01, 0.3), 1.5)
                _time.sleep(delay)


def reply(msg_id, text):
    m = find_message(msg_id)
    if not m:
        print(f"Message {msg_id} not found")
        return
    if m.get("status") == "replied":
        print(f"Message {msg_id} already replied; skipping to avoid duplicate send")
        return
    if m["source"] == "tg":
        send_tg(m["chat_id"], text, m.get("msg_id"))
    elif m["source"] == "wx":
        send_wx(m["from_user"], text, m.get("context_token"))
    store_mark_replied(msg_id, text)
    print(f"Replied to {msg_id}")


def mark_replied(msg_id):
    if store_mark_replied(msg_id):
        print(f"Marked {msg_id} as replied")
    else:
        print(f"Message {msg_id} not found")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "--list":
        list_pending()
    elif sys.argv[1] == "--mark" and len(sys.argv) > 2:
        mark_replied(sys.argv[2])
    elif len(sys.argv) >= 3:
        reply(sys.argv[1], " ".join(sys.argv[2:]))
    else:
        print("Usage: reply.py <msg_id> <text> | --list | --mark <msg_id>")
