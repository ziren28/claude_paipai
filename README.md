# 派派 Claude Paipai — AI 消息中枢

> 把 Claude Code CLI 变成 7×24 在线的私人 AI 助手，通过 Telegram / 微信随时随地远程操控。

**核心理念：** 利用 Claude Code 订阅（$100-200/月），零 API 费用，通过社交平台将消息**直达 Claude Code 主会话**，让 Claude 像真人助手一样接收指令、管理服务器、修复问题。

## 一键安装

```bash
git clone https://github.com/ziren28/claude_paipai.git
cd claude_paipai
bash install.sh
```

安装脚本引导完成：TG Bot 配置 → 微信扫码绑定 → 依赖安装 → 语音模型下载 → systemd 部署 → 快捷命令设置。

## 它能做什么

- 📨 **消息直达主会话** — TG/微信消息直接送入 Claude Code，Claude 实时看到并回复
- 📋 **菜单远程控制** — 硬重启 Claude、重启服务器、执行任意命令，手机上一键操作
- 🔄 **双通道消息** — Telegram + 微信 (iLink API) 统一收发
- 📢 **跨平台广播** — 微信消息自动转 TG，反之亦然
- 🎙️ **语音对话** — TG 发语音 → 自动识别 → Claude 回复 → 语音返回
- 🖥️ **远程运维** — 手机上执行 bash 命令、查看状态、管理 Docker
- 🌐 **Webhook API** — 监控系统推送告警，Claude 自动接收并处理
- 🔑 **SSH 多机管理** — 通过远程命令管理多台服务器
- 🌍 **浏览器自动化** — 配合 Chromium 容器实现网站签到、操作

---

## 核心：消息直达主会话

派派不是中间层 AI — **你的消息直接送到 Claude Code 主会话**。Claude 拥有完整的上下文、文件系统访问、bash 执行权限，能力远超任何 API 调用。

```
你 (TG/微信)  ──消息──→  poller.py  ──写入──→  messages.jsonl
                                                      ↓
                                             Claude Code 主会话
                                             (Monitor 实时监听)
                                                      ↓
                                             Claude 看到消息，思考，回复
                                                      ↓
                                             reply.py ──广播──→ TG + 微信
```

**Claude 在主会话中能做的一切，都可以通过手机触发：**
- 读写任意文件、执行 bash 命令
- git 操作、部署代码
- 分析日志、修复 bug
- 管理 Docker 容器
- SSH 到其他服务器

---

## 菜单系统 — 手机上的控制面板

在 TG 或微信发送 `/help` 查看完整菜单：

```
🤖 派派 Pulse — 消息中枢
━━━━━━━━━━━━━━━━━━
📋 消息管理
  /pending    查看待处理消息
  /clear      清除所有待处理
  /reply ID 内容  远程回复

🖥️ 系统运维
  /status     服务·进程·内存·磁盘
  /run 命令   执行任意 bash 命令
  /ps         进程列表
  /logs       最近 30 行日志
  /restart 服务  重启任意 systemd 服务

📊 快捷查询
  /mem  /disk  /uptime  /ip
━━━━━━━━━━━━━━━━━━
💡 消息前缀: /urgent 加急 /btw 低优先
```

### 硬重启 Claude Code

Claude 卡了？手机上直接操作：

```
你 (TG): /run tmux kill-session -t claude
派派: ✅ 会话已终止

你 (TG): /run tmux new -d -s claude "claude --dangerously-skip-permissions"
派派: ✅ Claude 已重启
```

### 重启服务器服务

```
你 (TG): /restart nginx
派派: ✅ nginx restarted, status: active

你 (TG): /restart inbox-poller
派派: ✅ 派派已重启
```

### 执行任意命令

```
你 (TG): /run docker ps
派派: NAMES         STATUS       PORTS
      myapp         Up 3 days    0.0.0.0:80->80/tcp
      postgres      Up 3 days    5432/tcp

你 (TG): /run cat /var/log/nginx/error.log | tail -20
派派: [返回最近20行错误日志]

你 (TG): /run reboot
派派: ⚠️ 服务器将重启...
```

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
                  │   poller.py     │  派派核心 (systemd 守护)
                  └────────┬────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ /command │ │  voice   │ │  text    │
        │ 直接执行  │ │ STT→AI  │ │ 直达主会话│
        │ 返回结果  │ │ →TTS    │ │ Claude处理│
        └──────────┘ └──────────┘ └──────────┘
                           │
                     ┌─────┴─────┐
                     ▼           ▼
                  TG 回复     微信回复
                     ↕ 跨平台广播 ↕
```

### 消息处理流程

```
1. 用户发消息 (TG / 微信 / Webhook)
       ↓
2. poller.py 实时接收
       ↓
3. 判断类型：
   ├─ /command  → 立即执行，返回结果
   ├─ 语音      → whisper 转写 → Claude 回复 → TTS 语音回复
   └─ 文字/图片  → 写入 messages.jsonl → Claude 主会话处理
                                           ↓
                                    reply.py → TG + 微信广播
```

---

## Webhook — 监控告警自动修复

派派提供 REST API（端口 8900），外部监控系统可以直接推送告警，**Claude 收到后自动开始排查和修复**。

### API 接口

```bash
# 健康检查
GET /api/health

# 查看待处理消息
GET /api/pending?token=your_secret

# 发送消息 — 告警直达 Claude 主会话
POST /api/message
{"token": "your_secret", "text": "告警内容", "source": "monitor"}

# 执行远程命令
POST /api/command
{"token": "your_secret", "command": "/status"}
```

### 示例 1：Nginx 异常自动修复

```bash
#!/bin/bash
# check_nginx.sh — cron 每分钟运行
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" https://yoursite.com)

if [ "$HTTP_CODE" != "200" ]; then
    curl -X POST http://localhost:8900/api/message \
        -H "Content-Type: application/json" \
        -d "{
            \"token\": \"your_secret\",
            \"text\": \"🚨 网站异常！HTTP $HTTP_CODE\\nhttps://yoursite.com 返回非200\\n请检查 nginx 和后端服务\",
            \"source\": \"nginx-monitor\"
        }"
fi
```

**效果：** 网站挂了 → 派派收到告警 → Claude 主会话看到消息 → Claude 自动检查 nginx logs、重启服务、定位问题 → 通过 TG/微信回复修复结果。

### 示例 2：SSL 证书到期提醒

```bash
#!/bin/bash
# check_ssl.sh — cron 每天运行
DOMAIN="yoursite.com"
EXPIRY=$(echo | openssl s_client -connect $DOMAIN:443 -servername $DOMAIN 2>/dev/null | openssl x509 -noout -enddate | cut -d= -f2)
EXPIRY_TS=$(date -d "$EXPIRY" +%s)
NOW_TS=$(date +%s)
DAYS_LEFT=$(( (EXPIRY_TS - NOW_TS) / 86400 ))

if [ "$DAYS_LEFT" -lt 7 ]; then
    curl -X POST http://localhost:8900/api/message \
        -H "Content-Type: application/json" \
        -d "{
            \"token\": \"your_secret\",
            \"text\": \"⚠️ SSL 证书将在 ${DAYS_LEFT} 天后到期\\n域名: $DOMAIN\\n到期: $EXPIRY\\n请续签证书\",
            \"source\": \"ssl-monitor\"
        }"
fi
```

### 示例 3：Docker 容器崩溃告警

```bash
#!/bin/bash
# check_docker.sh — cron 每 5 分钟运行
CONTAINERS="myapp postgres redis"

for c in $CONTAINERS; do
    STATUS=$(docker inspect -f '{{.State.Status}}' $c 2>/dev/null)
    if [ "$STATUS" != "running" ]; then
        curl -X POST http://localhost:8900/api/message \
            -H "Content-Type: application/json" \
            -d "{
                \"token\": \"your_secret\",
                \"text\": \"🚨 容器 $c 状态异常: $STATUS\\n请检查并重启\",
                \"source\": \"docker-monitor\"
            }"
    fi
done
```

### 示例 4：磁盘空间告警

```bash
#!/bin/bash
# check_disk.sh — cron 每小时运行
USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')

if [ "$USAGE" -gt 85 ]; then
    curl -X POST http://localhost:8900/api/message \
        -H "Content-Type: application/json" \
        -d "{
            \"token\": \"your_secret\",
            \"text\": \"⚠️ 磁盘使用率 ${USAGE}%\\n$(df -h / | tail -1)\\n请清理空间\",
            \"source\": \"disk-monitor\"
        }"
fi
```

### 示例 5：UptimeRobot / 外部监控接入

```bash
# UptimeRobot Webhook 设置:
# URL: http://your_server:8900/api/message
# POST Body:
# {"token":"your_secret","text":"🚨 *monitorFriendlyName* is *alertTypeFriendlyName*","source":"uptimerobot"}
```

**工作流：** 外部监控检测到异常 → Webhook 推送到派派 → 消息进入队列 → Claude 主会话实时收到 → Claude 自动 SSH 到目标服务器排查 → 修复后通过 TG/微信汇报结果。

---

## 实际应用场景

### 场景 1：手机远程开发
> 出门在外，手机微信收到客户反馈的 bug

```
你 (微信): 帮我看看 /root/myapp/server.py 第50行附近的报错
Claude: [在主会话中读取文件、分析代码]
        找到问题了，第52行的数据库连接字符串缺少端口号，已修复。
派派: → [同步回复到微信 + TG]
```

### 场景 2：凌晨告警处理
> 监控推送网站 500 错误，Claude 自动接管

```
[Webhook 告警] 🚨 yoursite.com 返回 HTTP 500
Claude: [自动开始排查]
        → 检查 nginx error.log → 发现后端 Python 进程 OOM
        → 增加 swap → 重启 gunicorn
        → 验证网站恢复正常
Claude (TG): "✅ 网站已恢复。原因：Python 进程 OOM，已增加 swap 并重启 gunicorn。"
```

### 场景 3：语音交互
> 开车时想问个技术问题

```
你 (TG 语音): "帮我解释一下 Python 的 asyncio 事件循环"
派派: 🎙️ [识别] → [Claude 思考] → [语音回复，小晓的声音]
```

### 场景 4：SSH 多机管理
> 手机上管理多台服务器

```
你 (TG): /run ssh web1 "systemctl status nginx && free -h"
派派: ● nginx active | Mem: 1.2G/4.0G

你 (TG): /run ssh db1 "pg_isready && du -sh /var/lib/postgresql"
派派: accepting connections | 42G
```

SSH 免密配置：
```bash
ssh-keygen -t ed25519
ssh-copy-id user@web1
ssh-copy-id user@db1
# ~/.ssh/config 配置 Host 别名
```

### 场景 5：浏览器自动化 + 定时签到
> 配合 Chromium 容器自动操作网站

```
# 部署 Chromium
docker run -d --name chromium -p 30001:3000 lscr.io/linuxserver/chromium:latest

# cron 每天签到
0 8 * * * /root/paipai/nodeseek_cron.sh

# 签到完成后派派推送:
# "📅 签到完成: +10 积分"
```

手动触发：
```
你 (TG): /run docker exec chromium python3 /config/checkin.py
派派: ✅ 签到成功
```

---

## 快速开始

### 方式一：一键安装（推荐）

```bash
git clone https://github.com/ziren28/claude_paipai.git
cd claude_paipai
bash install.sh
```

脚本自动完成：
1. 检查 Python / ffmpeg / tmux 环境
2. 安装 Python 依赖
3. 引导输入 **TG Bot Token** 和 **User ID**
4. 引导配置 **微信 iLink Bot**（可选，扫码绑定）
5. 下载语音识别模型 (~1.5GB)
6. 生成 `.env` 配置 + systemd 服务
7. 配置快捷命令并启动

### 方式二：手动安装

```bash
git clone https://github.com/ziren28/claude_paipai.git
cd claude_paipai
pip install httpx cryptography faster-whisper edge-tts
cp .env.example .env && vim .env
```

| 变量 | 说明 | 获取方式 |
|------|------|---------|
| `TG_TOKEN` | Telegram Bot Token | @BotFather 创建 Bot |
| `TG_OWNER` | 你的 TG User ID | @userinfobot 发消息 |
| `WX_STATE_FILE` | 微信 state.json 路径 | 微信 iLink Bot 扫码生成 |
| `WEBHOOK_TOKEN` | Webhook API 密钥 | 自定义字符串 |

---

## Claude Code 配置指南

### tmux 持久会话（推荐）

Claude Code 必须持续运行才能接收消息，tmux 保证 SSH 断开后不丢失：

```bash
# 创建会话
tmux new -s claude

# 在 tmux 内启动 Claude Code
claude

# 分离会话 (不会终止 Claude)
# 按 Ctrl+B 然后按 D

# 重新连接
tmux attach -t claude
```

**tmux 常用快捷键：**

| 按键 | 功能 |
|------|------|
| `Ctrl+B D` | 分离（后台运行）|
| `Ctrl+B [` | 滚动模式（q 退出）|
| `Ctrl+B c` | 新窗口 |
| `Ctrl+B n/p` | 下/上一个窗口 |

**推荐 tmux 配置 (~/.tmux.conf):**

```bash
set -g history-limit 50000      # 增大历史
set -g mouse on                 # 鼠标滚动
set -g status-right '#H | %Y-%m-%d %H:%M'
set -g default-terminal "screen-256color"
set -g detach-on-destroy off    # 防意外关闭
```

### Claude Code 自动模式

主会话需要自动权限，否则每个操作都需要手动确认，无法远程工作：

**方法一：沙箱 + 跳过权限（推荐）**

```bash
# 1. 启用沙箱 (~/.claude/settings.json)
{
  "sandbox": true
}

# 2. 启动时跳过确认
claude --dangerously-skip-permissions
```

> 启用沙箱后 root 用户也可以使用 `--dangerously-skip-permissions`。

**方法二：Allowlist 精细控制**

```json
// ~/.claude/settings.json
{
  "permissions": {
    "allow": [
      "Bash(git *)",
      "Bash(python3 *)",
      "Bash(systemctl *)",
      "Bash(docker *)",
      "Read", "Write", "Edit", "Glob", "Grep"
    ],
    "deny": [
      "Bash(rm -rf /)"
    ]
  }
}
```

**方法三：Bypass 模式**

在 Claude Code 界面中按 `Shift+Tab` 切换到 bypass permissions 模式。

### 主会话 + 派派联动

Claude Code 是「大脑」，派派是「耳朵和嘴巴」：

```bash
# 1. tmux 中启动 Claude
ccr    # 快捷命令，等于 tmux new -s claude "claude ..."

# 2. Claude 内说 "唤醒派派" — 自动挂载消息监听
# Claude 会执行:
#   Monitor tail -f poller.log | grep "✈️|💬|🌐|⚡|❌"
#   python3 reply.py --list

# 3. 收到消息时 Claude 自动看到通知
# 用 reply.py 回复:
#   python3 reply.py <id> "回复内容"
#   python3 stream_reply.py <id>   # 流式回复
```

### CLAUDE.md 提示文件

在工作目录创建 `CLAUDE.md`，Claude Code 每次启动自动加载：

```markdown
# 派派消息处理

## 启动
每次新会话执行：
1. `systemctl is-active inbox-poller`
2. `Monitor tail -f /root/paipai/poller.log | grep --line-buffered -E "✈️|💬|🌐|⚡|❌"`
3. `python3 /root/paipai/reply.py --list`

## 回复
- `python3 reply.py --list` 查看待处理
- `python3 reply.py <id> "内容"` 回复（自动广播 TG+微信）
- `python3 stream_reply.py <id>` 流式回复

## 告警处理
收到 Webhook 告警消息时，主动排查问题、修复、回复结果。
```

---

## 快捷命令

安装脚本自动配置，或手动添加到 `~/.bashrc`：

```bash
# Claude Code
alias cc='claude'
alias cca='claude --dangerously-skip-permissions'
alias ccr='tmux new -s claude "claude --dangerously-skip-permissions" || tmux attach -t claude'

# 派派
alias pp='python3 /root/paipai/reply.py'
alias pp-list='python3 /root/paipai/reply.py --list'
alias pp-log='tail -f /root/paipai/poller.log'
alias pp-restart='systemctl restart inbox-poller'
alias pp-status='systemctl status inbox-poller --no-pager'
```

日常使用：
```bash
ccr              # 一键进入 Claude (tmux 持久)
# 在 Claude 里说 "唤醒派派"

pp-list          # 查看消息
pp abc123 "好的" # 快速回复
pp-log           # 实时日志
```

---

## 文件说明

| 文件 | 功能 |
|------|------|
| `poller.py` | **核心** — TG/微信轮询 + 命令执行 + 语音对话 + Webhook |
| `reply.py` | 消息回复 + 跨平台广播 |
| `stream_reply.py` | Claude 流式回复 (TG 打字机 + 微信分块) |
| `msg_store.py` | 消息存储 (原子文件更新) |
| `claude_status.py` | Claude 进程状态监控 |
| `menu.py` | 菜单系统 |
| `install.sh` | 一键安装脚本 |

---

## 技术栈

- **Python 3.10+** — asyncio 异步架构
- **httpx** — 异步 HTTP 客户端
- **faster-whisper** — 语音识别 (medium, int8, CPU)
- **edge-tts** — 微软语音合成 (免费)
- **ffmpeg** — 音频转码
- **cryptography** — 微信图片 AES-ECB 解密
- **systemd** — 服务守护
- **tmux** — 持久会话

## 对比其他方案

| | claude_paipai | cc-connect | cc-weixin |
|---|---|---|---|
| 消息直达主会话 | ✅ | ❌ 纯桥接 | ❌ 纯桥接 |
| 菜单远程控制 | ✅ 硬重启/任意命令 | 部分 | ❌ |
| 跨平台广播 | ✅ | ❌ | ❌ |
| 语音对话 | ✅ | ❌ | ❌ |
| Webhook 告警 | ✅ 自动修复 | ❌ | ❌ |
| API 费用 | $0 (订阅制) | API 计费 | API 计费 |

---

## License

MIT
