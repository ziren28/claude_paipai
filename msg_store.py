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
    """Update specific fields of a message. Atomic write (tempfile + rename)."""
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
        except Exception:
            os.unlink(tmp)
            raise
    return found


def mark_replied(msg_id, reply_text=""):
    """Mark a message as replied with truncated reply preview."""
    preview = reply_text[:200] + "..." if len(reply_text) > 200 else reply_text
    return update_message(msg_id, status="replied", reply=preview)


def clear_all_pending():
    """Mark all pending messages as replied. Returns count cleared."""
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
        except Exception:
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
