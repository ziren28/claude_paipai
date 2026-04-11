# 派派 Claude Paipai — AI 消息中枢

> 把 Claude Code CLI 变成 7×24 在线的私人 AI 助手，通过 Telegram / 微信随时随地远程操控。

**核心理念：** 利用 Claude Code 订阅（$100-200/月），不花 API 费用，通过社交平台实现完整的 AI 代理体验 — 包括语音对话、远程运维、跨平台消息同步。

## 它能做什么

- 🔄 **双通道消息** — Telegram + 微信 (iLink API) 统一收发
- 📢 **跨平台广播** — 微信消息自动转 TG，TG 消息自动转微信
- 🤖 **AI 自动回复** — 收到消息秒级响应，Claude 主会话兜底复杂问题
- 🎙️ **语音对话** — TG 发语音 → 自动识别 → Claude 思考 → 语音回复
- 🖥️ **远程运维** — 手机上执行服务器命令、查看状态、重启服务
- ⚡ **流式回复** — TG 打字机效果实时输出，微信分块推送
- 🌐 **Webhook API** — 外部系统接入，自动化工作流

---

## 运作流程

### 整体架构

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Telegram    │    │    微信       │    │  Webhook API │
│  (语音/文字)  │    │ (iLink Bot)  │    │  (REST :8900)│
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           ▼
                  ┌─────────────────┐
                  │   poller.py     │  派派核心进程
                  │   (systemd)     │  持续运行，永不断线
                  └────────┬────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ /command │ │  voice   │ │  text    │
        │ 直接执行  │ │ STT→AI  │ │ 存入队列  │
        │ 返回结果  │ │ →TTS    │ │ AI回复   │
        └──────────┘ └──────────┘ └────┬─────┘
                                       ▼
                              ┌─────────────────┐
                              │ messages.jsonl   │
                              │ (消息队列)        │
                              └────────┬────────┘
                                       ▼
                              ┌─────────────────┐
                              │  Claude Code     │
                              │  主会话 (tmux)    │
                              │  Monitor 监听     │
                              └────────┬────────┘
                                       │
                                 reply.py / stream_reply.py
                                       │
                              ┌────────┴────────┐
                              ▼                 ▼
                         TG 回复            微信回复
                       (打字机效果)        (分块推送)
                              ↕ 跨平台广播 ↕
```

### 消息处理流程

```
1. 用户发消息 (TG/微信/Webhook)
       ↓
2. poller.py 实时接收
       ↓
3. 判断消息类型：
   ├─ /command  → 立即执行 bash 命令，返回结果
   ├─ 语音消息   → whisper 转写 → Claude 回复 → edge-tts 语音合成 → 发回
   └─ 文字/图片  → 存入 messages.jsonl (status: pending)
                      ↓
                  派派 AI 自动回复（轻量/全能模式）
                      ↓
                  Claude Code 主会话也可手动回复
                      ↓
                  reply.py 发送 → 双平台广播
```

### 语音对话流程

```
用户在 TG 发语音 (30秒内)
       ↓
poller.py 下载 .ogg 文件
       ↓
faster-whisper (medium模型, CPU)
├─ 自动检测语言 (zh/en)
└─ 转写为文字 (~10-15秒)
       ↓
Claude (paipai_think_full) 生成回复
       ↓
edge-tts 语音合成
├─ 中文 → XiaoxiaoNeural (微软小晓)
└─ 英文 → AriaNeural
       ↓
ffmpeg 转码 mp3 → ogg/opus
       ↓
TG sendVoice → 用户收到语音回复
```

---

## 实际应用场景

### 场景 1：手机远程开发
> 出门在外，手机微信收到客户反馈的 bug

```
你 (微信): 帮我看看 /root/myapp/server.py 第50行附近的报错
派派: [自动转发到 Claude Code 主会话]
Claude: 找到问题了，第52行的数据库连接字符串缺少端口号...
派派: [同步回复到微信 + TG]
```

### 场景 2：服务器运维
> 凌晨收到报警，不想开电脑

```
你 (TG): /status
派派: ✅ inbox-poller active | Claude 🟢空闲 | RAM 2.6G/7.8G | Disk 55%

你 (TG): /run docker logs myapp --tail 20
派派: [返回最近20行日志]

你 (TG): /restart myapp
派派: ✅ myapp restarted, status: active
```

### 场景 3：语音交互
> 开车时想问个技术问题

```
你 (TG 语音): "帮我解释一下 Python 的 asyncio 事件循环是怎么工作的"
派派: 🎙️ STT [zh]: 帮我解释一下Python的asyncio事件循环是怎么工作的
      → Claude 思考中...
      → [返回一段语音回复，用小晓的声音解释]
```

### 场景 4：跨平台同步
> 团队成员用不同平台

```
同事 (微信): 今天的部署进度怎么样了？
派派 AI: 📨 收到 | Claude 🟢空闲
         🤖 目前已完成数据库迁移和API部署，前端还在构建中...
         → [自动同步到 TG: [WX→TG] 🤖 ...]
```

### 场景 5：定时任务 + 通知
> 每天自动签到并汇报

```
# cron: 0 8 * * *
nodeseek_cron.sh 执行签到
  → 派派推送结果到 TG + 微信
  → "📅 NodeSeek 每日任务完成: +10 积分"
```

---

## 快速开始

### 前置条件

- Linux 服务器 (推荐 2vCPU / 8GB RAM)
- Python 3.10+
- Claude Code CLI 已安装且已登录
- Telegram Bot Token (@BotFather 创建)
- 微信 iLink Bot (可选)

### 1. 克隆项目

```bash
git clone https://github.com/ziren28/claude_paipai.git
cd claude_paipai
```

### 2. 安装依赖

```bash
pip install httpx cryptography faster-whisper edge-tts
```

### 3. 配置环境变量

```bash
cp .env.example .env
vim .env
```

```bash
TG_TOKEN=your_telegram_bot_token      # @BotFather 获取
TG_OWNER=your_telegram_user_id        # @userinfobot 获取
WX_STATE_FILE=/path/to/state.json     # 微信 iLink Bot 状态文件
WEBHOOK_TOKEN=your_webhook_secret     # Webhook API 认证密钥
```

### 4. 部署为 systemd 服务

```bash
cat > /etc/systemd/system/inbox-poller.service << 'EOF'
[Unit]
Description=Claude Paipai Message Hub
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/claude_paipai
EnvironmentFile=/path/to/claude_paipai/.env
ExecStart=/usr/bin/python3 poller.py
Restart=always
RestartSec=5
StandardOutput=append:/path/to/claude_paipai/poller.log
StandardError=append:/path/to/claude_paipai/poller.log

[Install]
WantedBy=multi-user.target
EOF

systemctl enable --now inbox-poller
```

---

## Claude Code 配置指南

### tmux 持久会话（推荐）

Claude Code 进程需要持续运行，tmux 保证 SSH 断开后不丢失：

```bash
# 创建会话
tmux new -s claude

# 在 tmux 内启动 Claude Code
claude

# 分离会话 (不会终止 Claude)
# 按 Ctrl+B 然后按 D

# 重新连接
tmux attach -t claude

# 常用快捷键
# Ctrl+B D    — 分离 (detach)
# Ctrl+B [    — 滚动模式 (q 退出)
# Ctrl+B c    — 新窗口
# Ctrl+B n/p  — 下/上一个窗口
```

**推荐 tmux 配置 (~/.tmux.conf):**

```bash
# 增大历史记录
set -g history-limit 50000

# 鼠标支持 (可滚动)
set -g mouse on

# 状态栏
set -g status-right '#H | %Y-%m-%d %H:%M'

# 256色支持
set -g default-terminal "screen-256color"

# 防止意外关闭
set -g detach-on-destroy off
```

### Claude Code 自动模式

在 Claude Code 中启用全自动权限，避免交互式确认阻断消息处理：

**方法一：启动参数**

```bash
# 跳过所有权限确认 (仅限可信环境)
claude --dangerously-skip-permissions
```

> ⚠️ root 用户无法直接使用此参数。解决方案：启用沙箱模式。

```bash
# 在 settings.json 中启用沙箱
# ~/.claude/settings.json
{
  "sandbox": true
}
```

启用沙箱后，Claude Code 在隔离环境中运行，root 用户也可以使用 `--dangerously-skip-permissions`。

**方法二：Allowlist 精细控制**

编辑 `~/.claude/settings.json`：

```json
{
  "permissions": {
    "allow": [
      "Bash(git *)",
      "Bash(python3 *)",
      "Bash(systemctl *)",
      "Bash(docker *)",
      "Read",
      "Write",
      "Edit",
      "Glob",
      "Grep"
    ],
    "deny": [
      "Bash(rm -rf /)",
      "Bash(curl * | bash)"
    ]
  }
}
```

**方法三：Bypass 模式 (推荐)**

在 Claude Code 交互界面中：
1. 输入 `/permissions`
2. 选择 `Allow all tools in this session`
3. 或按 `Shift+Tab` 切换到 bypass 模式

### 主会话 + 派派联动

Claude Code 主会话作为"大脑"，派派作为"耳朵和嘴巴"：

```bash
# 1. tmux 中启动 Claude Code
tmux new -s claude
claude

# 2. Claude 内挂载派派监听
# 使用 Monitor 工具持续监听新消息：
Monitor tail -f /path/to/poller.log | grep --line-buffered -E "✈️|💬|🌐|⚡|❌"

# 3. Claude 自动收到通知，用 reply.py 回复：
python3 reply.py --list
python3 reply.py <id> "回复内容"
python3 stream_reply.py <id>   # 流式回复
```

### CLAUDE.md 提示文件

在项目根目录创建 `CLAUDE.md`，Claude Code 启动时自动加载：

```markdown
# 派派消息处理指南

## 自动挂载
每次启动时执行：
1. 检查服务: `systemctl is-active inbox-poller`
2. 挂载监听: `Monitor tail -f /path/to/poller.log | grep --line-buffered -E "✈️|💬|🌐|⚡|❌"`
3. 查看待处理: `python3 reply.py --list`

## 消息回复
- 查看: `python3 reply.py --list`
- 回复: `python3 reply.py <id> "回复内容"`
- 标记: `python3 reply.py --mark <id>`
- 流式: `python3 stream_reply.py <id>`

## 跨平台广播
reply.py 自动广播到另一个平台，无需手动操作。
```

---

## 远程命令

在 TG 或微信中直接发送：

| 命令 | 功能 |
|------|------|
| `/help` | 显示帮助菜单 |
| `/status` | 服务状态 + 进程 + 内存 + 磁盘 |
| `/run <cmd>` | 执行任意 bash 命令 |
| `/ps` | 进程列表 (按内存排序) |
| `/mem` | 内存使用 |
| `/disk` | 磁盘使用 |
| `/logs` | 最近 30 行 poller 日志 |
| `/ip` | 公网 IP |
| `/uptime` | 系统运行时间 |
| `/restart <svc>` | 重启 systemd 服务 |
| `/pending` | 查看待处理消息 |
| `/clear` | 清空消息队列 |
| `/ai off/lite/full/auto` | 切换 AI 回复模式 |

**消息前缀：**
- `/urgent 内容` — 标记为加急
- `/btw 内容` — 标记为低优先

---

## Webhook API

端口 `8900`，支持外部系统接入：

```bash
# 健康检查
GET /api/health

# 查看待处理消息
GET /api/pending?token=your_secret

# 发送消息到队列
POST /api/message
{"token": "your_secret", "text": "hello", "source": "api"}

# 执行远程命令
POST /api/command
{"token": "your_secret", "command": "/status"}
```

---

## 文件说明

| 文件 | 功能 |
|------|------|
| `poller.py` | **核心** — TG/微信轮询 + 命令 + 语音 + AI回复 + Webhook |
| `reply.py` | 消息回复 + 跨平台广播 |
| `stream_reply.py` | Claude 流式回复 (TG 打字机 + 微信分块) |
| `msg_store.py` | 共享消息存储 (原子文件更新) |
| `claude_status.py` | Claude 进程状态实时监控 |
| `paipai_agent.py` | 派派 AI 轻量模式 (cc-bridge) |
| `paipai_full.py` | 派派 AI 全能模式 (claude -p) |
| `menu.py` | 交互式菜单系统 |

---

## 技术栈

- **Python 3.10+** — asyncio 异步架构
- **httpx** — 异步 HTTP 客户端
- **faster-whisper** — 语音识别 (medium 模型, int8 量化, CPU)
- **edge-tts** — 微软语音合成 (免费, 高质量中英文)
- **ffmpeg** — 音频转码
- **cryptography** — 微信图片 AES-ECB 解密
- **systemd** — 服务管理
- **tmux** — 持久会话

## 对比其他方案

| | claude_paipai | cc-connect | cc-weixin |
|---|---|---|---|
| 跨平台广播 | ✅ | ❌ | ❌ |
| AI 自动回复 | ✅ | ❌ | ❌ |
| 语音对话 | ✅ | ❌ | ❌ |
| 远程运维命令 | ✅ | 部分 | ❌ |
| Webhook API | ✅ | ❌ | ❌ |
| API 费用 | $0 (订阅制) | API 计费 | API 计费 |

---

## License

MIT
