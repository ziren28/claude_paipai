# 派派 Paipai — AI 消息中枢

统一消息中枢：TG + 微信双通道轮询、AI 自动回复、语音对话、跨平台广播、远程命令、Webhook API。

## 架构

```
TG / 微信 / Webhook
        ↓
   poller.py (派派核心)
   ├─ /commands → 直接执行返回结果
   ├─ voice → whisper STT → Claude → edge-tts TTS → 语音回复
   └─ text/image → messages.jsonl → auto_reply / 主会话处理
        ↓
   reply.py / stream_reply.py → TG + WX 跨平台广播
```

## 功能

- **双通道轮询** — Telegram + 微信 (iLink API) 统一收发
- **跨平台广播** — WX 消息回复自动转发 TG，反之亦然
- **AI 自动回复** — 派派 AI 即时响应，Claude 主会话兜底
- **语音对话** — TG 语音 → faster-whisper(medium) STT → Claude → edge-tts TTS → 语音回复
- **远程命令** — `/run` `/status` `/ps` `/mem` `/disk` `/restart` 等完整运维
- **流式回复** — TG 打字机效果 (editMessage) + 微信分块推送
- **Webhook API** — REST API 接入，支持消息发送和命令执行
- **图片处理** — AES-ECB 解密微信加密图片

## 快速开始

### 1. 安装依赖

```bash
pip install httpx cryptography faster-whisper edge-tts
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入你的 token
```

| 变量 | 说明 |
|------|------|
| `TG_TOKEN` | Telegram Bot Token (@BotFather 获取) |
| `TG_OWNER` | 你的 Telegram User ID |
| `WX_STATE_FILE` | 微信 iLink Bot state.json 路径 |
| `WEBHOOK_TOKEN` | Webhook API 认证密钥 |

### 3. 启动

```bash
# 直接运行
python3 poller.py

# 或用 systemd
cp inbox-poller.service /etc/systemd/system/
systemctl enable --now inbox-poller
```

## 使用

### 消息管理

```bash
python3 reply.py --list              # 查看待处理消息
python3 reply.py <id> "回复内容"      # 回复 (双向广播)
python3 reply.py --mark <id>         # 仅标记已处理
python3 stream_reply.py <id>         # Claude 流式回复
```

### 远程命令 (TG/微信发送)

```
/status     — 系统状态
/run <cmd>  — 执行 bash 命令
/ps         — 进程列表
/mem        — 内存使用
/disk       — 磁盘使用
/logs       — 查看日志
/restart <svc> — 重启服务
/pending    — 查看待处理消息
/clear      — 清空消息队列
```

### Webhook API (端口 8900)

```bash
# 健康检查
curl localhost:8900/api/health

# 发送消息
curl -X POST localhost:8900/api/message \
  -H "Content-Type: application/json" \
  -d '{"token":"your_secret","text":"hello","source":"api"}'

# 执行命令
curl -X POST localhost:8900/api/command \
  -H "Content-Type: application/json" \
  -d '{"token":"your_secret","command":"/status"}'
```

## 文件说明

| 文件 | 功能 |
|------|------|
| `poller.py` | 核心 — TG/微信轮询 + 命令处理 + 语音对话 + Webhook |
| `reply.py` | 消息回复 + 跨平台广播 |
| `stream_reply.py` | Claude 流式回复 (打字机效果) |
| `msg_store.py` | 共享消息存储 (原子更新) |
| `claude_status.py` | Claude 进程状态监控 |
| `paipai_agent.py` | 派派 AI 轻量回复 |
| `paipai_full.py` | Claude 完整回复 |
| `menu.py` | 菜单系统 |

## 语音对话

发送 Telegram 语音消息，派派自动：

1. **STT** — faster-whisper (medium, int8, CPU) 转写，自动检测中/英文
2. **AI** — Claude 生成回复
3. **TTS** — edge-tts 合成语音 (中文: XiaoxiaoNeural, 英文: AriaNeural)
4. **发送** — OGG/OPUS 格式通过 TG sendVoice 回复

## systemd 服务

```ini
[Unit]
Description=Paipai Message Hub
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/paipai
EnvironmentFile=/path/to/paipai/.env
ExecStart=/usr/bin/python3 poller.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## License

MIT
