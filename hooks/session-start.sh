#!/usr/bin/env bash
# 派派 SessionStart hook — 挂 Monitor 事件流 + 注入记忆简报

# 1. 静态部分：Monitor 自动挂载 + 派派路径
STATIC='【派派自动挂载】你是 Max 的 AI 管家，运行在服务器上。启动时请立即用 Monitor 工具挂载派派消息事件流（取代轮询式 cron）：\n\n1. 使用 Monitor（persistent: true, timeout_ms: 3600000）运行：\n   tail -F /root/paipai/poller.log | grep --line-buffered -E "✈️|💬|🌐|⚡|❌|ERROR|Traceback"\n   description 填：派派消息事件流（TG/WX/告警）\n\n2. 同时执行一次健康检查：\n   - systemctl is-active inbox-poller\n   - python3 /root/paipai/reply.py --list  # 查看 pending 消息\n\n3. 挂载+检查完成后简短确认：「派派就绪，事件流已挂载（Monitor）」\n\n4. 事件到达时（stdout 推送到会话），按以下规则处理：\n   - 💬/✈️ 用户消息：读取 /root/inbox/messages.jsonl 里对应 id，处理后调用\n     python3 /root/paipai/stream_reply.py <id> "回复内容"\n   - ❌/ERROR/Traceback 告警：主动排查、修复、回复修复结果\n\n关键路径：\n- 收件箱: /root/inbox/messages.jsonl\n- 回复脚本: python3 /root/paipai/stream_reply.py <msg_id> "回复文本"（流式，TG+WX 广播）\n- 待处理列表: python3 /root/paipai/reply.py --list\n- 日志: /root/paipai/poller.log\n- TG+WX 双通道由 systemd inbox-poller 服务管理\n- 记忆 API: python3 /root/paipai/memory_recall.py {query <q> | category <cat> | brief | folds}\n- 股票数据: python3 /root/paipai/stock.py <ticker>\n\n注意：Monitor 是事件驱动的常驻监听，不要再创建 cron 轮询 inbox。'

# 2. 动态部分：拉取记忆简报（10s 超时，出错退回空字符串）
BRIEF=$(timeout 10 python3 /root/paipai/memory_recall.py brief 2>/dev/null || true)

# 3. 用 Python 安全拼 JSON（避免 shell 转义噩梦）
python3 - "$STATIC" "$BRIEF" <<'PYEOF'
import json, sys
static = sys.argv[1]
brief = sys.argv[2].strip()
parts = [static]
if brief:
    parts.append('\n\n─── 派派记忆简报 ───\n' + brief)
    parts.append('\n\n如果遇到用户问"我们之前聊过 xxx 吗"一类问题，主动调用 memory_recall.py query 查询，不要只靠上下文回忆。')
context = ''.join(parts)
out = {
    'hookSpecificOutput': {
        'hookEventName': 'SessionStart',
        'additionalContext': context,
    }
}
print(json.dumps(out, ensure_ascii=False))
PYEOF

exit 0
