#!/usr/bin/env python3
"""
派派 AI Agent — 独立 AI 管家，用 claude -p sonnet 思考，Python 执行工具。

流程：
  用户消息 → 构建 prompt（身份+状态+记忆+消息）→ claude -p sonnet
  → 解析返回 → 如有 [EXEC:cmd] 工具调用 → Python 执行 → 再喂回
  → 最终回复推送给用户
"""
import asyncio
import json
import subprocess
import time
import re
from pathlib import Path

# ======================== Config ========================

MEMORY_FILE = "/root/inbox/paipai_memory.json"
STATUS_FILE = "/root/inbox/claude_status.json"
ACTION_LOG = "/root/inbox/actions.log"
MODEL = "sonnet"
MAX_TOOL_ROUNDS = 3  # 最多工具调用轮数

# ======================== Identity Prompt ========================

SYSTEM_PROMPT = """你是派派，一个运行在 Linux 服务器上的 AI 管家。你的主人叫 Max。

## 你的身份
- 名字：派派 (Pulse)
- 角色：AI 管家，负责服务器管理、消息中转、日常协助
- 性格：简洁高效、友善、有幽默感、用中文回复
- 你运行在 GCP 服务器 cc.maxcole.app 上

## 你的能力
你可以通过工具执行操作。在回复中使用以下格式调用工具：

[EXEC:命令] — 执行 bash 命令并获取结果
[NOTIFY:消息] — 向主人发送通知（用于重要提醒）
[REMEMBER:内容] — 记住重要信息
[QUEUE:消息] — 将任务排队到主会话 Claude 处理

## 规则
1. 简单问题直接回答（闲聊、状态查询、知识问答）
2. 需要执行操作时使用 [EXEC:] 工具
3. 复杂编程/开发任务用 [QUEUE:] 转交主会话
4. 回复简洁，不要超过 200 字
5. 不要编造数据，不确定就用 [EXEC:] 查询
6. 危险操作（rm -rf, 格式化等）拒绝执行"""

# ======================== Memory ========================

def load_memory():
    if Path(MEMORY_FILE).exists():
        try:
            return json.loads(Path(MEMORY_FILE).read_text())
        except Exception:
            pass
    return {"notes": [], "user_prefs": {}}


def save_memory(mem):
    Path(MEMORY_FILE).write_text(json.dumps(mem, ensure_ascii=False, indent=2))


def add_memory(content):
    mem = load_memory()
    mem["notes"].append({
        "content": content,
        "ts": time.strftime("%Y-%m-%d %H:%M"),
    })
    # Keep last 50
    mem["notes"] = mem["notes"][-50:]
    save_memory(mem)


# ======================== Context Builder ========================

def build_context():
    """Build current system + Claude status context."""
    parts = []

    # Machine status
    try:
        cpu = subprocess.run("top -bn1 | grep 'Cpu(s)' | awk '{print $2}'", shell=True, capture_output=True, text=True, timeout=5).stdout.strip()
        mem = subprocess.run("free -h | awk 'NR==2{print $3\"/\"$2}'", shell=True, capture_output=True, text=True, timeout=5).stdout.strip()
        disk = subprocess.run("df -h / | awk 'NR==2{print $3\"/\"$2}'", shell=True, capture_output=True, text=True, timeout=5).stdout.strip()
        uptime = subprocess.run("uptime -p", shell=True, capture_output=True, text=True, timeout=5).stdout.strip()
        parts.append(f"服务器: CPU {cpu}% | 内存 {mem} | 磁盘 {disk} | {uptime}")
    except Exception:
        parts.append("服务器: 状态获取失败")

    # Claude status
    try:
        cs = json.loads(Path(STATUS_FILE).read_text())
        parts.append(f"Claude 主会话: {cs['label']} | PID {cs.get('pid','-')} | CPU {cs.get('cpu',0)}%")
        if cs.get("task"):
            parts.append(f"当前任务: {cs['task']}")
    except Exception:
        parts.append("Claude 主会话: 状态未知")

    # Services
    try:
        svcs = []
        for name, cmd in [("派派", "inbox-poller"), ("cc-bridge", None), ("CRS", "claude-relay")]:
            if cmd:
                r = subprocess.run(f"systemctl is-active {cmd}", shell=True, capture_output=True, text=True, timeout=3).stdout.strip()
            else:
                r = subprocess.run("docker inspect cc-bridge --format '{{.State.Status}}' 2>/dev/null", shell=True, capture_output=True, text=True, timeout=3).stdout.strip()
            svcs.append(f"{name}:{r}")
        parts.append("服务: " + " | ".join(svcs))
    except Exception:
        pass

    # Memory summary
    mem = load_memory()
    if mem["notes"]:
        recent = mem["notes"][-3:]
        mem_text = "; ".join([n["content"][:50] for n in recent])
        parts.append(f"记忆: {mem_text}")

    # Pending messages
    try:
        from msg_store import list_pending
        pending = list_pending()
        if pending:
            parts.append(f"待处理消息: {len(pending)} 条")
    except Exception:
        pass

    return "\n".join(parts)


# ======================== Tool Executor ========================

def execute_tools(text):
    """Parse and execute tool calls in AI response. Returns (clean_text, tool_results, actions)."""
    results = []
    actions = []
    clean = text

    # [EXEC:command]
    for match in re.finditer(r'\[EXEC:(.+?)\]', text):
        cmd = match.group(1).strip()
        # Safety check
        dangerous = ["rm -rf /", "mkfs", "dd if=", "> /dev/sd", ":(){ :|:&"]
        if any(d in cmd for d in dangerous):
            results.append(f"[EXEC:{cmd}] → 危险命令已拦截")
            continue
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            output = (r.stdout + r.stderr).strip()[:1000]
            results.append(f"[EXEC:{cmd}] → {output}")
        except subprocess.TimeoutExpired:
            results.append(f"[EXEC:{cmd}] → 超时")
        except Exception as e:
            results.append(f"[EXEC:{cmd}] → 错误: {e}")
        clean = clean.replace(match.group(0), "")

    # [REMEMBER:content]
    for match in re.finditer(r'\[REMEMBER:(.+?)\]', text):
        content = match.group(1).strip()
        add_memory(content)
        actions.append(f"已记住: {content[:50]}")
        clean = clean.replace(match.group(0), "")

    # [NOTIFY:message]
    for match in re.finditer(r'\[NOTIFY:(.+?)\]', text):
        msg = match.group(1).strip()
        actions.append(f"通知: {msg}")
        clean = clean.replace(match.group(0), "")

    # [QUEUE:message]
    for match in re.finditer(r'\[QUEUE:(.+?)\]', text):
        msg = match.group(1).strip()
        actions.append(f"已排队到主会话: {msg[:50]}")
        clean = clean.replace(match.group(0), "")

    return clean.strip(), results, actions


# ======================== AI Call ========================

BRIDGE_URL = "http://127.0.0.1:5674/v1/messages"
BRIDGE_KEY = "sk-lW66YXGUZMMV7SNbwia6itfev6Fx6p3dzAvLza9oBd4kn0mIj5WfuTQXdye1B"

async def call_ai(user_msg, context, history=None):
    """Call cc-bridge API with system prompt + context + user message."""
    import httpx

    system_parts = [
        SYSTEM_PROMPT,
        f"\n## 当前状态\n{context}",
        f"\n## 当前时间\n{time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if history:
        system_parts.append(f"\n## 工具执行结果\n" + "\n".join(history))

    system_text = "\n".join(system_parts)

    body = {
        "model": f"claude-{MODEL}-4-6",
        "max_tokens": 1024,
        "stream": False,
        "system": system_text,
        "messages": [{"role": "user", "content": user_msg}],
    }

    async with httpx.AsyncClient() as client:
        # cc-bridge forces streaming, so we consume SSE and extract text
        async with client.stream(
            "POST", BRIDGE_URL,
            json=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {BRIDGE_KEY}",
                "anthropic-version": "2023-06-01",
                "Accept-Encoding": "identity",
            },
            timeout=60,
        ) as resp:
            if resp.status_code != 200:
                await resp.aread()
                return f"AI 调用失败 (status={resp.status_code})"

            full_text = ""
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    try:
                        event = json.loads(line[6:])
                        etype = event.get("type", "")
                        if etype == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                full_text += delta.get("text", "")
                        elif etype == "error":
                            return f"AI 错误: {event.get('error', {}).get('message', '')}"
                    except json.JSONDecodeError:
                        continue

            return full_text or "派派暂时无法回复"


# ======================== Main Agent Loop ========================

async def process_message(user_msg):
    """
    Main agent entry point. Returns final reply text.
    Handles multi-round tool calls.
    """
    context = build_context()
    history = []

    for round_num in range(MAX_TOOL_ROUNDS + 1):
        # Call AI
        reply = await call_ai(user_msg, context, history if history else None)

        if not reply:
            return "派派暂时无法回复，请稍后再试"

        # Check for tool calls
        has_tools = bool(re.search(r'\[(EXEC|REMEMBER|NOTIFY|QUEUE):', reply))

        if not has_tools or round_num >= MAX_TOOL_ROUNDS:
            # No tools or max rounds — return final reply
            clean, _, actions = execute_tools(reply)  # execute any remaining
            # Log
            _log(f"回复: {clean[:60]}")
            return clean or reply

        # Execute tools and feed results back
        clean, results, actions = execute_tools(reply)
        history.extend(results)

        for a in actions:
            _log(a)

    return reply


def _log(msg):
    ts = time.strftime("%m-%d %H:%M:%S")
    with open(ACTION_LOG, "a") as f:
        f.write(f"{ts} [派派AI] {msg}\n")


# ======================== Sync wrapper ========================

def process_message_sync(user_msg):
    """Sync wrapper for async process_message."""
    return asyncio.run(process_message(user_msg))


if __name__ == "__main__":
    import sys
    msg = " ".join(sys.argv[1:]) or "你好"
    print(process_message_sync(msg))
