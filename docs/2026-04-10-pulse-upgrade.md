# Pulse System Upgrade — P0/P1/P2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Pulse to support cross-platform message sync, enhanced remote commands, and architecture optimizations.

**Architecture:** Three parallel improvements to the existing poller/reply system: (1) stream_reply.py broadcasts replies to both TG and WX regardless of source, (2) poller.py gains `/pending`, `/reply`, `/clear` remote commands, (3) messages.jsonl gets line-level status updates instead of full-file rewrites.

**Tech Stack:** Python 3.11, httpx (async), asyncio, systemd

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `/root/inbox/poller.py` | Modify | P1: Add `/pending`, `/reply`, `/clear` commands |
| `/root/inbox/stream_reply.py` | Modify | P0: Broadcast replies to both platforms |
| `/root/inbox/reply.py` | Modify | P2: Line-level JSONL updates |
| `/root/inbox/msg_store.py` | Create | P2: Shared message store with atomic line updates |

---

### Task 1: P2 — Extract shared message store (msg_store.py)

Both `reply.py` and `stream_reply.py` duplicate load/save/mark logic and both do full-file rewrites. Extract to a shared module with line-level updates.

**Files:**
- Create: `/root/inbox/msg_store.py`
- Modify: `/root/inbox/stream_reply.py:40-67` (remove duplicated store functions)
- Modify: `/root/inbox/reply.py:29-43` (remove duplicated store functions)

- [ ] **Step 1: Create msg_store.py with atomic line-level updates**

```python
#!/usr/bin/env python3
"""Shared message store for Pulse inbox."""
import json
import tempfile
import os
from pathlib import Path

INBOX = "/root/inbox/messages.jsonl"

def load_messages():
    msgs = []
    if not Path(INBOX).exists():
        return msgs
    for line in Path(INBOX).read_text().splitlines():
        if line.strip():
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return msgs

def find_message(msg_id):
    for m in load_messages():
        if m["id"] == msg_id:
            return m
    return None

def update_message(msg_id, **fields):
    """Update specific fields of a message by rewriting only the matching line.
    Uses atomic write (tempfile + rename) for safety."""
    path = Path(INBOX)
    if not path.exists():
        return False
    lines = path.read_text().splitlines()
    found = False
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("id") == msg_id:
            m.update(fields)
            lines[i] = json.dumps(m, ensure_ascii=False)
            found = True
            break
    if found:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(lines) + "\n")
            os.replace(tmp, str(path))
        except:
            os.unlink(tmp)
            raise
    return found

def mark_replied(msg_id, reply_text=""):
    """Mark a message as replied with truncated reply preview."""
    preview = reply_text[:200] + "..." if len(reply_text) > 200 else reply_text
    return update_message(msg_id, status="replied", reply=preview)

def clear_all_pending():
    """Mark all pending messages as replied."""
    msgs = load_messages()
    count = 0
    for m in msgs:
        if m.get("status") == "pending":
            m["status"] = "replied"
            m["reply"] = "(cleared)"
            count += 1
    if count > 0:
        path = Path(INBOX)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                for m in msgs:
                    f.write(json.dumps(m, ensure_ascii=False) + "\n")
            os.replace(tmp, str(path))
        except:
            os.unlink(tmp)
            raise
    return count

def list_pending():
    """Return list of pending messages, sorted by priority."""
    msgs = load_messages()
    pending = [m for m in msgs if m.get("status") == "pending"]
    prio_order = {"urgent": 0, "normal": 1, "btw": 2}
    pending.sort(key=lambda m: prio_order.get(m.get("priority", "normal"), 1))
    return pending
```

- [ ] **Step 2: Verify msg_store.py loads correctly**

Run: `cd /root/inbox && python3 -c "from msg_store import load_messages, list_pending; print(f'{len(list_pending())} pending')"`
Expected: `51 pending` (or current count)

- [ ] **Step 3: Update stream_reply.py to use msg_store**

Replace lines 40-67 in stream_reply.py with:
```python
from msg_store import find_message, mark_replied
```
Remove the old `load_messages`, `save_messages`, `find_message`, `mark_replied` functions.

- [ ] **Step 4: Update reply.py to use msg_store**

Replace lines 29-43 and the `mark_replied`/`save_messages` functions with:
```python
from msg_store import load_messages, find_message, mark_replied as store_mark_replied, list_pending as store_list_pending, update_message
```
Update `list_pending()`, `reply()`, `mark_replied()` to use the shared store.

- [ ] **Step 5: Verify both scripts still work**

Run: `cd /root/inbox && python3 reply.py --list | head -5`
Run: `cd /root/inbox && python3 -c "from stream_reply import find_message; print(find_message('53a327da0740'))"`

- [ ] **Step 6: Restart poller and verify no breakage**

Run: `systemctl restart inbox-poller && sleep 1 && systemctl is-active inbox-poller`

---

### Task 2: P0 — Cross-platform reply broadcast

When replying to a message (from any source), broadcast the final reply to BOTH TG and WX.

**Files:**
- Modify: `/root/inbox/stream_reply.py:285-333` (main function)
- Modify: `/root/inbox/reply.py:138-151` (reply function)
- Modify: `/root/inbox/poller.py:83-111` (reuse send functions)

- [ ] **Step 1: Add broadcast helper to stream_reply.py**

After the existing streamer classes (~line 194), add:

```python
# ======================== Cross-Platform Broadcast ========================

# WeChat owner info (for broadcasting)
WX_OWNER = WX_STATE.get("owner_user_id", "")

async def broadcast_final(client: httpx.AsyncClient, msg: dict, text: str):
    """Send final reply to the OTHER platform (cross-sync)."""
    source = msg["source"]
    prefix = f"[来自{source.upper()}]\n"
    broadcast_text = prefix + text

    if source == "tg" and WX_OWNER:
        # TG message answered → also send to WX
        await _send_wx(client, WX_OWNER, broadcast_text)
    elif source == "wx":
        # WX message answered → also send to TG
        await _send_tg(client, TG_OWNER_CHAT, broadcast_text)

TG_OWNER_CHAT = 7712845902  # same as TG_OWNER in poller

async def _send_tg(client, chat_id, text):
    for i in range(0, len(text), 4096):
        await client.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id, "text": text[i:i+4096],
        }, timeout=15)

async def _send_wx(client, to_user, text):
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
```

- [ ] **Step 2: Call broadcast after streamer.finish() in main()**

In the `main()` function of stream_reply.py, after `await streamer.finish(final_text)` add:

```python
        # Broadcast to other platform
        try:
            await broadcast_final(client, msg, final_text)
        except Exception as e:
            print(f"Broadcast error: {e}", flush=True)
```

- [ ] **Step 3: Add broadcast to reply.py**

In `reply()` function, after sending to source platform, also send to the other:

```python
def reply(msg_id, text):
    msgs = load_messages()
    for m in msgs:
        if m["id"] == msg_id:
            # Send to source
            if m["source"] == "tg":
                send_tg(m["chat_id"], text, m.get("msg_id"))
                # Broadcast to WX
                wx_owner = WX_STATE.get("owner_user_id", "")
                if wx_owner:
                    send_wx(wx_owner, f"[来自TG]\n{text}")
            elif m["source"] == "wx":
                send_wx(m["from_user"], text, m.get("context_token"))
                # Broadcast to TG
                send_tg(7712845902, f"[来自WX]\n{text}")
            store_mark_replied(msg_id, text)
            print(f"Replied to {msg_id} (broadcast)")
            return
    print(f"Message {msg_id} not found")
```

- [ ] **Step 4: Test broadcast with a real message**

Send a test message from WX, use stream_reply to answer it, verify both WX and TG receive the reply.

Run: `python3 /root/inbox/stream_reply.py <recent_wx_msg_id> "测试跨平台广播"`

---

### Task 3: P1 — Enhanced remote commands

Add `/pending`, `/reply <id> <text>`, `/clear`, `/sessions` commands to poller.

**Files:**
- Modify: `/root/inbox/poller.py:71-169` (command handler section)

- [ ] **Step 1: Add msg_store import to poller.py**

At the top of poller.py, after the existing imports, add:
```python
sys.path.insert(0, INBOX_DIR)
from msg_store import list_pending, mark_replied, clear_all_pending
```
Wait — poller.py runs with cwd=/root/inbox already (systemd WorkingDirectory). Just add:
```python
from msg_store import list_pending, mark_replied, clear_all_pending
```

- [ ] **Step 2: Add new commands to handle_command()**

Add these command handlers inside `handle_command()`, before the `else: return False`:

```python
    elif cmd == "/pending":
        pending = list_pending()
        if not pending:
            help_text = "没有待处理消息"
        else:
            import datetime
            lines = []
            for m in pending[:20]:  # limit 20
                src = m["source"].upper()
                ts = datetime.datetime.fromtimestamp(m["ts"]).strftime("%H:%M")
                txt = m.get("text", "")[:50]
                img = " [IMG]" if m.get("image") else ""
                lines.append(f"[{src}] {m['id']} ({ts}){img}: {txt}")
            help_text = f"待处理 {len(pending)} 条:\n" + "\n".join(lines)
        bash_cmd = None

    elif cmd == "/clear":
        count = clear_all_pending()
        help_text = f"已清除 {count} 条待处理消息"
        bash_cmd = None

    elif cmd == "/reply" and len(parts) > 1:
        # /reply <msg_id> <text>
        reply_parts = parts[1].split(None, 1)
        if len(reply_parts) < 2:
            help_text = "用法: /reply <msg_id> <text>"
        else:
            rid, rtext = reply_parts
            # Use stream_reply or direct reply
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
```

- [ ] **Step 3: Update /help text**

Add the new commands to the help text:
```python
            "/pending - 查看待处理消息",
            "/clear - 清除所有待处理消息",
            "/reply <id> <text> - 远程回复消息",
```

- [ ] **Step 4: Verify syntax and restart**

Run: `python3 -c "import py_compile; py_compile.compile('/root/inbox/poller.py', doraise=True)" && echo OK`
Run: `systemctl restart inbox-poller && sleep 1 && systemctl is-active inbox-poller`

- [ ] **Step 5: Test from WeChat or TG**

Send `/pending` from WX or TG, verify it returns the pending list.
Send `/clear` to clear all old pending messages.

---

### Task 4: Cleanup and verification

- [ ] **Step 1: Clear 51 stale pending messages**

Run: `python3 -c "from msg_store import clear_all_pending; print(clear_all_pending())"`

- [ ] **Step 2: Full integration test**

1. Send a text from WX → verify TG also gets it (via poller log)
2. Reply from terminal with stream_reply → verify both WX and TG get the reply
3. Send `/status` from TG → verify command response
4. Send `/pending` from WX → verify pending list
5. Restart poller: `systemctl restart inbox-poller`

- [ ] **Step 3: Update Pulse skill with new capabilities**

Update `/root/.claude/skills/pulse/SKILL.md` to document new commands and cross-platform sync.

- [ ] **Step 4: Commit summary**

All changes:
- `msg_store.py` — new shared message store with atomic updates
- `stream_reply.py` — uses msg_store, broadcasts to both platforms
- `reply.py` — uses msg_store, broadcasts to both platforms
- `poller.py` — new commands: `/pending`, `/clear`, `/reply`
