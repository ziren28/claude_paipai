#!/usr/bin/env python3
"""
Claude 状态监控 — 定期检测 Claude 进程状态，写入状态文件供派派读取。
以 systemd 服务运行，每 5 秒更新一次。
"""
import json
import os
import subprocess
import time
import re
from pathlib import Path

STATUS_FILE = "/root/inbox/claude_status.json"
POLL_INTERVAL = 5  # seconds


def atomic_write_json(path: str, data: dict) -> None:
    p = Path(path)
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, p)


def _run(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return ""


def detect_status():
    """Detect Claude's current state."""
    # Find claude process (not tmux, not gateway)
    ps = _run("ps aux | grep -E '^root.*claude$' | grep -v grep | grep -v tmux | grep -v gateway")
    if not ps:
        # Try broader match
        ps = _run("ps aux | grep '[0-9] claude$' | head -1")

    if not ps:
        return {
            "state": "offline",
            "label": "离线",
            "pid": None,
            "cpu": 0,
            "mem": 0,
            "uptime": "",
            "task": "",
            "ts": time.time(),
        }

    parts = ps.split()
    pid = parts[1] if len(parts) > 1 else ""
    cpu = float(parts[2]) if len(parts) > 2 else 0
    mem = float(parts[3]) if len(parts) > 3 else 0

    # Get uptime
    uptime = _run(f"ps -p {pid} -o etime= 2>/dev/null").strip()

    # Get tmux pane content to detect activity
    pane = _run("tmux capture-pane -t claude -p -S -10 2>/dev/null")
    lines = pane.splitlines() if pane else []

    # Detect what Claude is doing
    task = ""
    state = "idle"
    label = "空闲"

    # Check for spinner/activity indicators
    last_lines = "\n".join(lines[-5:]) if lines else ""

    if cpu > 10:
        state = "busy"
        label = "忙碌"
    elif cpu > 3:
        state = "thinking"
        label = "思考中"

    # Detect specific activities from pane content
    if "Thinking" in last_lines or "Stewing" in last_lines or "✻" in last_lines:
        state = "thinking"
        label = "思考中"
    elif "Running" in last_lines or "Executing" in last_lines:
        state = "busy"
        label = "执行中"
    elif "Edit" in last_lines and ("file" in last_lines.lower() or "/" in last_lines):
        state = "busy"
        label = "编辑文件"
    elif "error" in last_lines.lower() or "Error" in last_lines:
        state = "error"
        label = "异常"

    # Try to extract current task/prompt from pane
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("❯ ") or line.startswith("> "):
            task = line[2:].strip()[:80]
            break
        if line.startswith("Human:") or line.startswith("user:"):
            task = line.split(":", 1)[1].strip()[:80]
            break

    # If idle, check how long idle (from last activity)
    if state == "idle" and "❯" in last_lines:
        label = "空闲 (等待输入)"

    return {
        "state": state,
        "label": label,
        "pid": int(pid) if pid.isdigit() else None,
        "cpu": cpu,
        "mem": mem,
        "uptime": uptime,
        "task": task,
        "ts": time.time(),
    }


def main():
    while True:
        try:
            status = detect_status()
            atomic_write_json(STATUS_FILE, status)
        except Exception as e:
            atomic_write_json(STATUS_FILE, {
                "state": "error", "label": f"监控异常: {e}",
                "pid": None, "cpu": 0, "mem": 0, "uptime": "", "task": "", "ts": time.time(),
            })
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
