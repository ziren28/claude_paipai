"""
派派交互式菜单系统 v2 — 最多两级菜单，预设管理，功能丰富
"""
import json
import subprocess
import time
from pathlib import Path

# ======================== Persistent Presets ========================

PRESET_FILE = "/root/inbox/presets.json"

def _load_presets():
    if Path(PRESET_FILE).exists():
        try:
            return json.loads(Path(PRESET_FILE).read_text())
        except Exception:
            pass
    # Default presets
    return [
        {"name": "官方直连", "url": "", "key": "", "builtin": True},
        {"name": "本地网关", "url": "http://127.0.0.1:5674", "key": "sk-lW66YXGUZMMV7SNbwia6itfev6Fx6p3dzAvLza9oBd4kn0mIj5WfuTQXdye1B", "builtin": False},
    ]

def _save_presets(presets):
    Path(PRESET_FILE).write_text(json.dumps(presets, ensure_ascii=False, indent=2))

# ======================== Session State ========================

_sessions = {}
SESSION_TIMEOUT = 300

def _key(source, **ctx):
    if source == "tg":
        return f"tg:{ctx.get('chat_id', '')}"
    return f"wx:{ctx.get('from_user', '')[-12:]}"

def _get(source, **ctx):
    k = _key(source, **ctx)
    s = _sessions.get(k)
    if s and time.time() - s["ts"] > SESSION_TIMEOUT:
        del _sessions[k]
        return None
    return s

def _set(source, state, data=None, **ctx):
    k = _key(source, **ctx)
    if k not in _sessions:
        _sessions[k] = {"state": state, "data": data or {}, "ts": time.time()}
    else:
        _sessions[k]["state"] = state
        if data is not None:
            _sessions[k]["data"].update(data)
        _sessions[k]["ts"] = time.time()

def _clear(source, **ctx):
    _sessions.pop(_key(source, **ctx), None)

# ======================== Helpers ========================

ACTION_LOG = "/root/inbox/actions.log"

def _log_action(action, source="system"):
    """Log all menu operations."""
    import datetime
    ts = datetime.datetime.now().strftime("%m-%d %H:%M:%S")
    line = f"{ts} [{source}] {action}\n"
    with open(ACTION_LOG, "a") as f:
        f.write(line)

def _run(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return (r.stdout + r.stderr).strip() or "done"
    except subprocess.TimeoutExpired:
        return "超时"
    except Exception as e:
        return str(e)

def _get_claude_env():
    try:
        r = subprocess.run(
            "tmux show-environment -t claude 2>/dev/null | grep -E 'ANTHROPIC_'",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        envs = {}
        for line in r.stdout.strip().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                envs[k] = v
        return envs
    except Exception:
        return {}

def _apply_config(url, key):
    cmds = []
    if url:
        cmds.append(f"tmux set-environment -t claude ANTHROPIC_BASE_URL '{url}'")
    else:
        cmds.append("tmux set-environment -t claude -u ANTHROPIC_BASE_URL 2>/dev/null; true")
    if key:
        cmds.append(f"tmux set-environment -t claude ANTHROPIC_API_KEY '{key}'")
    else:
        cmds.append("tmux set-environment -t claude -u ANTHROPIC_API_KEY 2>/dev/null; true")
    for cmd in cmds:
        subprocess.run(cmd, shell=True, timeout=5, capture_output=True)

def _restart_claude():
    subprocess.run("tmux send-keys -t claude C-c 2>/dev/null", shell=True, timeout=5, capture_output=True)
    subprocess.run("sleep 1 && tmux send-keys -t claude 'claude' Enter 2>/dev/null", shell=True, timeout=5, capture_output=True)

def _claude_status():
    """Read Claude status from monitor."""
    try:
        data = json.loads(Path("/root/inbox/claude_status.json").read_text())
        return data
    except Exception:
        return {"state": "unknown", "label": "未知", "pid": None, "cpu": 0, "mem": 0, "uptime": "", "task": ""}

def _status_icon(state):
    return {"idle": "🟢", "busy": "🔴", "thinking": "🟡", "error": "🔴", "offline": "⚫"}.get(state, "⚪")

def _mask(s, show=12):
    if not s or len(s) <= show:
        return s or "无"
    return s[:show] + "..."

# ======================== Main Handler ========================

def handle_menu(text: str, source: str, **ctx) -> str | None:
    text = text.strip()

    # Enter menu — /menu, /m, 派派
    if text.lower() in ("/menu", "/m") or text in ("派派", "pp"):
        _set(source, "main", {}, **ctx)
        return _render_main()

    # Quick shortcuts — 直达子菜单
    # 派派1 or /m1 → 接入点, pp2 → 服务
    # Only trigger if "派派" followed immediately by a digit or known keyword (no space)
    shortcut = None
    for prefix in ("派派", "pp", "/m"):
        if text.startswith(prefix) and len(text) > len(prefix):
            rest = text[len(prefix):]
            # Must start with digit or known keyword, no leading space
            if rest[0].isdigit() or rest in ("接入", "接入点", "服务", "状态", "claude", "控制", "日志", "消息", "待处理", "命令", "bash", "ai", "AI"):
                shortcut = rest
                break
    if shortcut:
        if shortcut in ("1", "接入", "接入点"):
            _set(source, "preset_list", {}, **ctx)
            return _render_preset_list()
        elif shortcut in ("2", "服务"):
            _set(source, "service", {}, **ctx)
            return _render_service()
        elif shortcut in ("3", "状态"):
            return _render_status()
        elif shortcut in ("4", "claude", "控制"):
            _set(source, "claude_ctrl", {}, **ctx)
            return _render_claude_ctrl()
        elif shortcut in ("5", "日志"):
            logs = _run("tail -15 /root/inbox/poller.log")
            return f"📋 最近日志:\n{logs}"
        elif shortcut in ("6", "消息", "待处理"):
            from msg_store import list_pending as _lp
            pending = _lp()
            if not pending:
                return "✅ 没有待处理消息"
            import datetime
            lines = []
            for m in pending[:10]:
                icon = {"tg": "✈️", "wx": "💬"}.get(m["source"], "📨")
                ts = datetime.datetime.fromtimestamp(m["ts"]).strftime("%H:%M")
                lines.append(f"{icon} {m['id']} {ts} {m.get('text','')[:35]}")
            return f"📬 待处理 {len(pending)} 条:\n" + "\n".join(lines)
        elif shortcut in ("7", "命令", "bash"):
            _set(source, "bash_input", {}, **ctx)
            return "💻 输入 Bash 命令:\n（输入后会先预览确认再执行）"
        elif shortcut in ("8", "ai", "AI"):
            _set(source, "ai_mode", {}, **ctx)
            return _render_ai_mode()

    # Quick exit
    if text.lower() in ("/q", "/cancel"):
        if _get(source, **ctx):
            _clear(source, **ctx)
            return "✅ 已退出菜单"
        return None

    session = _get(source, **ctx)
    if not session:
        return None

    state = session["state"]
    data = session["data"]

    # ═══ Level 1: Main Menu ═══
    if state == "main":
        if text == "1":  # Claude 接入点
            _set(source, "preset_list", **ctx)
            return _render_preset_list()
        elif text == "2":  # 服务管理
            _set(source, "service", **ctx)
            return _render_service()
        elif text == "3":  # 系统状态
            return _render_status() + "\n\n" + _render_main()
        elif text == "4":  # Claude 状态
            _set(source, "claude_ctrl", **ctx)
            return _render_claude_ctrl()
        elif text == "5":  # 查看日志
            logs = _run("tail -15 /root/inbox/poller.log")
            return f"📋 最近日志:\n{logs}\n\n发 /menu 返回"
        elif text == "6":  # 查看待处理
            from msg_store import list_pending
            pending = list_pending()
            if not pending:
                return "✅ 没有待处理消息\n\n发 /menu 返回"
            import datetime
            lines = []
            for m in pending[:10]:
                icon = {"tg": "✈️", "wx": "💬"}.get(m["source"], "📨")
                ts = datetime.datetime.fromtimestamp(m["ts"]).strftime("%H:%M")
                txt = m.get("text", "")[:35]
                lines.append(f"{icon} {m['id']} {ts} {txt}")
            return f"📬 待处理 {len(pending)} 条:\n" + "\n".join(lines) + "\n\n发 /menu 返回"
        elif text == "7":  # 自定义命令
            _set(source, "bash_input", {}, **ctx)
            return "💻 输入 Bash 命令:\n（输入后会先预览确认再执行）"
        elif text == "8":  # AI 模式
            _set(source, "ai_mode", **ctx)
            return _render_ai_mode()
        elif text == "0":
            _clear(source, **ctx)
            return "✅ 已退出菜单"
        else:
            return "请输入数字选择\n\n" + _render_main()

    # ═══ Level 2: Preset List ═══
    elif state == "preset_list":
        presets = _load_presets()

        if text == "0":
            _set(source, "main", **ctx)
            return _render_main()

        # Switch preset
        if text.startswith("s") and text[1:].isdigit():
            idx = int(text[1:]) - 1
            if 0 <= idx < len(presets):
                p = presets[idx]
                _log_action(f"切换接入点: {p['name']}", source)
                _apply_config(p["url"], p["key"])
                _restart_claude()
                _clear(source, **ctx)
                return f"✅ 已切换到: {p['name']}\n{'URL: ' + p['url'] if p['url'] else '官方直连'}\nClaude 正在重启..."
            return "序号无效"

        # Delete preset
        if text.startswith("d") and text[1:].isdigit():
            idx = int(text[1:]) - 1
            if 0 <= idx < len(presets):
                if presets[idx].get("builtin"):
                    return "内置预设不能删除"
                removed = presets.pop(idx)
                _save_presets(presets)
                return f"🗑️ 已删除: {removed['name']}\n\n" + _render_preset_list()
            return "序号无效"

        # Add new preset
        if text == "+":
            _set(source, "add_name", {}, **ctx)
            return "📝 新建预设\n请输入预设名称:"

        # Edit preset
        if text.startswith("e") and text[1:].isdigit():
            idx = int(text[1:]) - 1
            if 0 <= idx < len(presets):
                _set(source, "edit_choose", {"edit_idx": idx}, **ctx)
                p = presets[idx]
                return (
                    f"✏️ 编辑: {p['name']}\n"
                    f"URL: {p['url'] or '官方直连'}\n"
                    f"Key: {_mask(p['key'])}\n\n"
                    "1. 修改名称\n"
                    "2. 修改 URL\n"
                    "3. 修改 Key\n"
                    "0. 返回"
                )
            return "序号无效"

        return "请输入操作\n\n" + _render_preset_list()

    # ═══ Level 2: Add Preset - Name ═══
    elif state == "add_name":
        _set(source, "add_url", {"add_name": text}, **ctx)
        return f"名称: {text}\n请输入 API URL:\n(例如 http://127.0.0.1:5674)"

    elif state == "add_url":
        url = text if text.startswith("http") else ""
        if not text.startswith("http") and text != "-":
            return "请输入有效 URL（http开头）或 - 跳过"
        if text == "-":
            url = ""
        _set(source, "add_key", {"add_url": url}, **ctx)
        return f"URL: {url or '官方直连'}\n请输入 API Key:\n(输入 - 跳过)"

    elif state == "add_key":
        key = "" if text == "-" else text
        presets = _load_presets()
        new_preset = {
            "name": data.get("add_name", "未命名"),
            "url": data.get("add_url", ""),
            "key": key,
            "builtin": False,
        }
        presets.append(new_preset)
        _save_presets(presets)
        _set(source, "preset_list", {}, **ctx)
        return f"✅ 已添加预设: {new_preset['name']}\n\n" + _render_preset_list()

    # ═══ Level 2: Edit Preset ═══
    elif state == "edit_choose":
        idx = data.get("edit_idx", 0)
        presets = _load_presets()
        if idx >= len(presets):
            _set(source, "preset_list", {}, **ctx)
            return _render_preset_list()
        if text == "1":
            _set(source, "edit_name", **ctx)
            return "请输入新名称:"
        elif text == "2":
            _set(source, "edit_url", **ctx)
            return "请输入新 URL (- 清空):"
        elif text == "3":
            _set(source, "edit_key", **ctx)
            return "请输入新 Key (- 清空):"
        elif text == "0":
            _set(source, "preset_list", {}, **ctx)
            return _render_preset_list()
        return "请输入 1-3 或 0 返回"

    elif state in ("edit_name", "edit_url", "edit_key"):
        idx = data.get("edit_idx", 0)
        presets = _load_presets()
        if idx < len(presets):
            if state == "edit_name":
                presets[idx]["name"] = text
            elif state == "edit_url":
                presets[idx]["url"] = "" if text == "-" else text
            elif state == "edit_key":
                presets[idx]["key"] = "" if text == "-" else text
            _save_presets(presets)
        _set(source, "preset_list", {}, **ctx)
        return f"✅ 已更新\n\n" + _render_preset_list()

    # ═══ Level 2: Bash Command ═══
    elif state == "bash_input":
        # Dangerous command check
        dangerous = ["rm -rf /", "mkfs", "dd if=", "> /dev/sd", ":(){ :|:&", "chmod -R 777 /"]
        for d in dangerous:
            if d in text:
                return f"⚠️ 危险命令被拦截: {d}\n请重新输入或发 0 返回"
        _set(source, "bash_confirm", {"bash_cmd": text}, **ctx)
        return (
            "💻 命令预览:\n"
            "━━━━━━━━━━━━━━━━\n"
            f"$ {text}\n"
            "━━━━━━━━━━━━━━━━\n"
            "y 确认执行 | n 取消 | e 重新输入"
        )

    elif state == "bash_confirm":
        cmd = data.get("bash_cmd", "")
        if text.lower() in ("y", "yes", "确认"):
            _log_action(f"执行命令: {cmd}", source)
            output = _run(cmd)
            _clear(source, **ctx)
            return f"$ {cmd}\n\n{output[:3000]}"
        elif text.lower() in ("e", "edit", "重新"):
            _set(source, "bash_input", {}, **ctx)
            return "💻 请重新输入命令:"
        else:
            _clear(source, **ctx)
            return "已取消"

    # ═══ Level 2: Claude Control ═══
    elif state == "claude_ctrl":
        if text == "1":  # 查看状态
            status = _run("ps aux | grep '[c]laude' | grep -v grep | awk '{print $11, $12, $13}' | head -3")
            pid = _run("ps aux | grep '[c]laude' | grep -v grep | awk '{print $2}' | head -1")
            mem = _run("ps aux | grep '[c]laude' | grep -v grep | awk '{print $4\"%\"}' | head -1")
            cpu = _run("ps aux | grep '[c]laude' | grep -v grep | awk '{print $3\"%\"}' | head -1")
            uptime = _run("ps -p $(ps aux | grep '[c]laude' | grep -v grep | awk '{print $2}' | head -1) -o etime= 2>/dev/null || echo '未运行'")
            if not status.strip():
                _log_action("查看状态: 未运行", source)
                return "Claude 未运行\n\n" + _render_claude_ctrl()
            _log_action("查看状态: 运行中", source)
            return f"Claude 运行中\nPID: {pid}\nCPU: {cpu} | 内存: {mem}\n运行时间: {uptime}\n\n" + _render_claude_ctrl()
        elif text == "2":  # 重启(保存会话)
            _log_action("重启 Claude (保存会话)", source)
            # Ctrl+C 会让 Claude 保存会话再退出, 然后 -c 恢复
            _run("tmux send-keys -t claude C-c 2>/dev/null")
            _run("sleep 2 && tmux send-keys -t claude 'claude -c' Enter 2>/dev/null")
            _clear(source, **ctx)
            return "🔄 Claude 重启中 (会话已保存，用 -c 恢复)"
        elif text == "3":  # 中止当前任务
            _log_action("中止当前任务 (Ctrl+C)", source)
            _run("tmux send-keys -t claude C-c 2>/dev/null")
            _clear(source, **ctx)
            return "⏹️ 已中止当前任务\nClaude 仍在运行，可继续输入新指令"
        elif text == "4":  # 启动
            _log_action("启动 Claude", source)
            _run("tmux send-keys -t claude 'claude -c' Enter 2>/dev/null")
            _clear(source, **ctx)
            return "▶️ Claude 正在启动 (恢复上次会话)"
        elif text == "5":  # 强制杀死
            _log_action("强制杀死并重启", source)
            _run("pkill -f 'node.*claude' 2>/dev/null; sleep 1; tmux send-keys -t claude 'claude -c' Enter 2>/dev/null")
            _clear(source, **ctx)
            return "💀 已强制终止并重启"
        elif text == "6":  # Claude 日志
            # 获取 tmux 里最近的输出
            _run("tmux capture-pane -t claude -p -S -30 > /tmp/claude_pane.txt 2>/dev/null")
            logs = _run("cat /tmp/claude_pane.txt 2>/dev/null | tail -20")
            _log_action("查看 Claude 日志", source)
            return f"📋 Claude 终端 (最近20行):\n━━━━━━━━━━━━━━━━\n{logs[:2000]}\n━━━━━━━━━━━━━━━━\n\n" + _render_claude_ctrl()
        elif text == "7":  # 操作日志
            logs = _run("tail -20 /root/inbox/actions.log 2>/dev/null || echo '暂无操作日志'")
            return f"📝 操作日志 (最近20条):\n━━━━━━━━━━━━━━━━\n{logs}\n━━━━━━━━━━━━━━━━\n\n" + _render_claude_ctrl()
        elif text == "0":
            _set(source, "main", **ctx)
            return _render_main()
        return "请输入数字选择\n\n" + _render_claude_ctrl()

    # ═══ Level 2: AI Mode ═══
    elif state == "ai_mode":
        import poller
        if text == "1":
            poller.PAIPAI_MODE = "auto"
            _log_action("AI 模式 → 自动", source)
            _clear(source, **ctx)
            return "🤖 已切换: 自动模式\n正常→AI回复 | 出错→静默存inbox"
        elif text == "2":
            poller.PAIPAI_MODE = "lite"
            _log_action("AI 模式 → 轻量", source)
            _clear(source, **ctx)
            return "🤖 已切换: 轻量模式 (cc-bridge API)"
        elif text == "3":
            poller.PAIPAI_MODE = "off"
            _log_action("AI 模式 → 关闭", source)
            _clear(source, **ctx)
            return "🤖 已关闭 AI\n消息将存入 inbox 等主会话处理\n发 /ai auto 或菜单可重新开启"
        elif text == "0":
            _set(source, "main", **ctx)
            return _render_main()
        return "请输入数字选择\n\n" + _render_ai_mode()

    # ═══ Level 2: Service Menu ═══
    elif state == "service":
        actions = {
            "1": ("派派", "systemctl restart inbox-poller"),
            "2": ("cc-bridge", "docker restart cc-bridge"),
            "3": ("CRS", "systemctl restart claude-relay"),
            "4": ("Proxy", "systemctl restart claude-proxy"),
            "5": ("全部", "docker restart cc-bridge; systemctl restart claude-relay claude-proxy inbox-poller"),
        }
        if text in actions:
            name, cmd = actions[text]
            _log_action(f"重启服务: {name}", source)
            _run(cmd)
            _clear(source, **ctx)
            return f"🔄 {name} 已重启"
        elif text == "0":
            _set(source, "main", **ctx)
            return _render_main()
        return "请输入数字选择\n\n" + _render_service()

    _clear(source, **ctx)
    return None


# ======================== Renderers ========================

def _render_main():
    envs = _get_claude_env()
    url = envs.get("ANTHROPIC_BASE_URL", "官方直连")
    cs = _claude_status()
    icon = _status_icon(cs["state"])
    task_line = f"\n当前任务: {cs['task']}" if cs.get("task") else ""
    return (
        "🤖 派派控制中心\n"
        "━━━━━━━━━━━━━━━━\n"
        f"{icon} Claude: {cs['label']}"
        f" | CPU {cs['cpu']}% MEM {cs['mem']}%\n"
        f"接入: {url}"
        f"{task_line}\n"
        "━━━━━━━━━━━━━━━━\n"
        "1. 🔌 Claude 接入点\n"
        "2. 🔧 服务管理\n"
        "3. 📊 系统状态\n"
        "4. 🖥️ Claude 控制\n"
        "5. 📋 查看日志\n"
        "6. 📬 待处理消息\n"
        "7. 💻 执行命令\n"
        "8. 🤖 AI 模式\n"
        "0. 退出\n"
        "━━━━━━━━━━━━━━━━\n"
        "输入数字选择"
    )

def _render_preset_list():
    presets = _load_presets()
    lines = [
        "🔌 Claude 接入点\n"
        "━━━━━━━━━━━━━━━━"
    ]
    for i, p in enumerate(presets):
        url_display = p["url"][:30] if p["url"] else "官方直连"
        tag = " ⭐" if p.get("builtin") else ""
        lines.append(f"{i+1}. {p['name']}{tag}\n   {url_display}")
    lines.append(
        "━━━━━━━━━━━━━━━━\n"
        "s数字 切换 (如 s1)\n"
        "e数字 编辑 (如 e2)\n"
        "d数字 删除 (如 d2)\n"
        "+    新建预设\n"
        "0    返回主菜单"
    )
    return "\n".join(lines)

def _render_ai_mode():
    try:
        import poller
        current = poller.PAIPAI_MODE
    except Exception:
        current = "unknown"
    labels = {"auto": "自动 (推荐)", "lite": "轻量", "full": "全能", "off": "关闭"}
    return (
        f"🤖 AI 模式 [当前: {labels.get(current, current)}]\n"
        "━━━━━━━━━━━━━━━━\n"
        "1. 自动 — AI正常→回复，出错→静默\n"
        "2. 轻量 — 始终用 API 回复\n"
        "3. 关闭 — 消息存inbox等主会话\n"
        "0. 返回主菜单\n"
        "━━━━━━━━━━━━━━━━\n"
        "输入数字选择"
    )

def _render_claude_ctrl():
    cs = _claude_status()
    icon = _status_icon(cs["state"])
    status_line = f"{cs['label']} | PID {cs['pid'] or '-'} | CPU {cs['cpu']}%"
    task_line = f"\n任务: {cs['task']}" if cs.get("task") else ""
    return (
        f"🖥️ Claude 控制\n"
        f"{icon} {status_line}{task_line}\n"
        "━━━━━━━━━━━━━━━━\n"
        "1. 查看状态\n"
        "2. 重启 (保存会话)\n"
        "3. 中止当前任务\n"
        "4. 启动\n"
        "5. 强制杀死并重启\n"
        "6. 查看 Claude 日志\n"
        "7. 查看操作日志\n"
        "0. 返回主菜单\n"
        "━━━━━━━━━━━━━━━━\n"
        "输入数字选择"
    )

def _render_service():
    return (
        "🔧 服务管理\n"
        "━━━━━━━━━━━━━━━━\n"
        "1. 重启派派\n"
        "2. 重启 cc-bridge\n"
        "3. 重启 CRS\n"
        "4. 重启 Proxy\n"
        "5. 全部重启\n"
        "0. 返回主菜单\n"
        "━━━━━━━━━━━━━━━━\n"
        "输入数字选择"
    )

def _render_status():
    claude_procs = _run("ps aux | grep -c '[c]laude'")
    poller = _run("systemctl is-active inbox-poller")
    bridge = _run("docker inspect cc-bridge --format '{{.State.Status}}' 2>/dev/null || echo stopped")
    crs = _run("systemctl is-active claude-relay")
    proxy = _run("systemctl is-active claude-proxy")
    mem = _run("free -h | awk 'NR==2{print $3\"/\"$2}'")
    disk = _run("df -h / | awk 'NR==2{print $3\"/\"$2\" (\"$5\")\"}'")
    uptime = _run("uptime -p")

    envs = _get_claude_env()
    url = envs.get("ANTHROPIC_BASE_URL", "官方直连")

    return (
        "📊 系统状态\n"
        "━━━━━━━━━━━━━━━━\n"
        f"Claude 接入: {url}\n"
        f"Claude 进程: {claude_procs}\n"
        f"派派: {poller}\n"
        f"cc-bridge: {bridge}\n"
        f"CRS: {crs}\n"
        f"Proxy: {proxy}\n"
        f"内存: {mem}\n"
        f"磁盘: {disk}\n"
        f"运行: {uptime}\n"
        "━━━━━━━━━━━━━━━━"
    )
