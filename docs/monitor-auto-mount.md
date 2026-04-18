# Monitor 事件流自动挂载

Claude Code 启动时通过 **SessionStart hook** 自动挂载派派的 TG/WX/告警事件流，取代旧的 cron 轮询方案。消息到达即以通知方式推送进主会话，Claude 立刻响应。

## 为什么用 Monitor 而不是 cron

| 维度 | cron 轮询 | Monitor 事件流 |
|---|---|---|
| 触发方式 | 每 N 秒读 inbox | `tail -F` 日志，行到即触发 |
| 延迟 | 最坏 N 秒 | 亚秒级（pipe buffer 约 200 ms） |
| 上下文成本 | 每次拉取都消耗 token | 仅命中过滤规则时才消耗 |
| 轰炸风险 | 批量消息同时处理 → 并发 AI 调用 | per-user 锁 + 陈旧消息过滤，天然串行 |
| 实现 | 独立 cron 脚本 + 状态文件 | 一行 `tail \| grep` |

## Hook 脚本位置

`/root/paipai/hooks/session-start.sh` —— Claude Code 启动时读取并注入 `additionalContext`。

需要在 `~/.claude/settings.json` 登记：

```json
{
  "hooks": {
    "SessionStart": {
      "command": "/root/paipai/hooks/session-start.sh"
    }
  }
}
```

## Hook 注入的指令要点

启动时 Claude 会收到一条 SessionStart 上下文，要求它：

1. **立即挂载 Monitor（persistent, timeout_ms=3600000）**：
   ```bash
   tail -F /root/paipai/poller.log | grep --line-buffered -E "✈️|💬|🌐|⚡|❌|ERROR|Traceback"
   ```
   覆盖成功路径（✈️ TG、💬 WX、🌐 webhook、⚡ 命令执行）与失败路径（❌ ERROR Traceback）—— "silence is not success" 原则。

2. **执行一次健康检查**：
   - `systemctl is-active inbox-poller`
   - `python3 /root/paipai/reply.py --list`

3. **确认就绪**："派派就绪，事件流已挂载（Monitor）"

4. **事件分流**：
   - `💬/✈️` 用户消息 → 读 inbox 里对应 id → `python3 /root/paipai/stream_reply.py <id> "回复内容"`
   - `❌/ERROR/Traceback` → 主动排查、修复、回复修复结果

## Monitor 过滤规则设计

过滤规则必须**覆盖所有终态**，不能只匹配 happy path：

```bash
grep --line-buffered -E "✈️|💬|🌐|⚡|❌|ERROR|Traceback"
```

- `✈️` TG 通道事件（收到消息、通道就绪）
- `💬` 微信事件
- `🌐` webhook HTTP 请求
- `⚡` 远程命令执行
- `❌` 显式错误标记
- `ERROR` / `Traceback` 兜底捕获 Python 异常

反面教材：

```bash
# ❌ 错误 — 只匹配正常消息。崩溃/hang/OOM 全部静默
tail -F poller.log | grep --line-buffered "💬"
```

如果一时无法枚举所有失败签名，**宁可宽泛不要漏**（多报比漏报好）。

## 轰炸防线（2026-04-18 补强）

用户报告过"消息轰炸导致 SSH 异常断开"事件。根因是：

1. poller 重启后 TG offset 归零，重拉所有历史 update
2. 进程内去重缓存 `_recent_msgs` 重启即丢
3. `reply.py` 跨平台广播 `[TG→WX]` / `[WX→TG]` 回环，echo 再次被 getupdates 吞回
4. 同一用户多条消息并发触发多个 AI 调用

修复对应四层防线：

1. **`.poller_state.json` 落盘**：`recent_msgs` + `tg_offset` 持久化，启动时恢复。
2. **WX 指纹用 `context_token`**：WX 没有 msg_id，服务端重投使用同一 token，作为稳定指纹。
3. **去跨平台广播**：`reply.py` / `stream_reply.py` 仅回源平台。
4. **`auto_reply` 陈旧消息跳过 + per-user asyncio.Lock**：>5 min 的消息直接跳过，并发消息到来时 lock busy 就跳过。
5. **Echo guard**：`ECHO_PREFIXES`（"📨 收到"、"🤖 "、"[TG→WX]"…）出现在入口即丢弃，避免自己回自己。

## 手动验证

```bash
# 启动派派
systemctl restart inbox-poller

# 查看状态持久化
cat /root/paipai/.poller_state.json
# {"recent_msgs": [], "tg_offset": 123456}

# 查看日志（过滤同 Monitor 规则）
tail -F /root/paipai/poller.log | grep --line-buffered -E "✈️|💬|🌐|⚡|❌|ERROR|Traceback"

# 查看 pending 并回复
python3 /root/paipai/reply.py --list
python3 /root/paipai/reply.py <msg_id> "直接回复文本"      # 不经 AI
python3 /root/paipai/stream_reply.py <msg_id>              # 走 claude -p 流式生成
```

## 关键路径速查

| 文件 | 作用 |
|---|---|
| `/root/paipai/poller.py` | TG + WX 双通道轮询，写入 inbox |
| `/root/paipai/stream_reply.py` | 调用 `claude -p` 流式回复（支持打字机效果） |
| `/root/paipai/reply.py` | 直接发送文本回复（不经 AI） |
| `/root/paipai/hooks/session-start.sh` | SessionStart hook，注入 Monitor 挂载指令 |
| `/root/inbox/messages.jsonl` | 消息收件箱（append-only JSONL） |
| `/root/paipai/.poller_state.json` | TG offset + WX dedup 指纹（runtime，已 gitignore） |
| `/root/paipai/wechat/state.json` | WX bot_token（含敏感信息，已 gitignore） |
