# 派派 Claude Paipai — 把微信变成你的终端

> 在微信输入，就像在终端输入一样。

```
你 (微信): 帮我装个 nginx 然后部署我的网站
Claude: 正在安装... ✅ 已部署到 http://your-ip:80
```

就这么简单。不需要 SSH 客户端，不需要打开电脑，一条微信消息，Claude 帮你搞定一切。

---

## 30 秒了解派派

**派派是什么？** 一个轻量级 Python 脚本（~800行），把 Telegram/微信消息直接送进 Claude Code 主会话。

**它不是什么？** 不是 API wrapper，不是中间层 AI。你的消息直达 Claude Code —— Claude 拥有完整的终端权限，能做终端能做的一切。

**花多少钱？** $0 额外费用。只需要你已有的 Claude Code 订阅。

```
┌──────────┐         ┌──────────┐         ┌──────────────┐
│  微信/TG  │ ──消息──→│  派派     │ ──直达──→│  Claude Code  │
│  手机打字  │ ←─回复──│  800行脚本│ ←─回复──│  完整终端权限  │
└──────────┘         └──────────┘         └──────────────┘
```

---

## 你能用它做什么

### 📱 微信/TG 当终端用

```
你: 看看服务器状态
Claude: CPU 12% | RAM 2.6G/8G | Disk 55% | 运行 23 天

你: 帮我装个 Redis
Claude: 正在安装... apt install redis-server
        ✅ Redis 6.0.16 已启动，端口 6379

你: 把我的项目部署一下，代码在 /root/myapp
Claude: 检查代码... 安装依赖... 配置 nginx 反代...
        ✅ 已部署到 http://35.xxx.xxx.xxx
        访问地址: https://yoursite.com (SSL 已配置)
```

### 🎙️ 说句话就行

TG 发语音，派派自动识别，Claude 回复，语音播报。开车、走路、躺床上都能操控服务器。

### 🔔 网站挂了？Claude 自己修

```bash
# 一个 cron 脚本监控你的网站
# 挂了 → Webhook 通知派派 → Claude 自动排查 → 修好了通知你
curl -X POST localhost:8900/api/message \
  -d '{"token":"secret","text":"🚨 网站 500 了"}'

# 你躺着就行，Claude 在干活
# 5 分钟后收到微信:
# "✅ 已修复。原因: Python OOM，已加 swap 并重启服务。"
```

### 🖥️ 手机上硬控服务器

```
你: /run docker ps
你: /restart nginx
你: /run tmux kill-session -t claude   ← Claude 卡了？杀掉重来
你: /run reboot                        ← 服务器重启
```

你甚至可以用微信重启 Claude 自己。

---

## 三个关键词

### 🪶 轻量

- 一个 Python 脚本，800 行
- 依赖就 4 个 pip 包
- 不需要 Docker、不需要数据库、不需要 K8s
- systemd 守护，挂了自动重启

### 🎮 可玩性

- 微信发消息 = 终端输入，想干什么干什么
- 语音对话 — 对着手机说话，Claude 语音回答
- 浏览器自动化 — 配合 Chromium 容器自动签到
- Webhook — 接入任何监控系统，实现自动化运维
- SSH 跳板 — 通过一台机器管理所有服务器

### 🧩 扩展性

- 加个 cron 脚本 = 定时任务
- 加个 Webhook = 监控告警
- 加个 SSH config = 多机管理
- 加个 Chromium = 浏览器操控
- 改几行 Python = 想要什么功能自己加

派派只是一个消息通道。真正的能力来自 Claude Code —— 它能做什么，你就能通过微信做什么。

---

## 安装：丢给 Claude 一句话

最优雅的安装方式：打开 Claude Code，把下面这段话粘贴进去。

```
帮我安装 claude_paipai 项目（GitHub: https://github.com/ziren28/claude_paipai）。

要求：
1. git clone 到 /root/paipai
2. 安装 Python 依赖（httpx cryptography faster-whisper edge-tts）
3. 创建 .env 文件，问我要 TG Bot Token 和 User ID
4. 配置 systemd 服务 inbox-poller，设置 EnvironmentFile 加载 .env
5. 配置 tmux：history-limit 50000, mouse on
6. 添加快捷命令到 ~/.bashrc：
   - cc='claude'
   - cca='claude --dangerously-skip-permissions'  
   - ccr='tmux + claude 自动模式'
   - pp/pp-list/pp-log/pp-restart 消息管理
7. 启动服务，验证 poller 正常运行
8. 在 CLAUDE.md 中配置派派自动挂载提示

微信 iLink Bot 配置可以先跳过。装完后告诉我怎么用。
```

Claude 会帮你搞定一切。是的，**用 Claude 安装一个让你远程操控 Claude 的工具**。

### 或者一键脚本

```bash
git clone https://github.com/ziren28/claude_paipai.git
cd claude_paipai && bash install.sh
```

交互式引导：TG 配置 → 微信扫码 → 依赖安装 → 模型下载 → 启动。

---

## 装完之后

### 1. 启动 Claude

```bash
ccr    # 一键：tmux + Claude 自动模式
```

### 2. 唤醒派派

在 Claude 里说：

```
唤醒派派
```

Claude 会自动挂载消息监听。

### 3. 手机上试试

打开 TG，给你的 Bot 发一条消息：

```
你好
```

Claude 收到了。从此，你的微信和 TG 就是终端。

---

## CLAUDE.md — 让 Claude 自动挂载派派

放在你的工作目录，Claude 每次启动自动加载：

```markdown
# 派派消息中枢

## 启动时自动执行
1. `systemctl is-active inbox-poller` — 检查服务
2. `Monitor tail -f /root/paipai/poller.log | grep --line-buffered -E "✈️|💬|🌐|⚡|❌"` — 挂载监听
3. `python3 /root/paipai/reply.py --list` — 查看待处理

## 收到消息时
- `python3 /root/paipai/reply.py <id> "回复"` — 回复（自动广播 TG+微信）
- `python3 /root/paipai/stream_reply.py <id>` — 流式回复（打字机效果）

## 收到告警时
主动排查问题、修复、回复修复结果。
```

---

## 菜单命令

手机上发送 `/help`：

```
📋 消息管理    /pending  /clear  /reply
🖥️ 系统运维    /status  /run  /ps  /logs  /restart
📊 快捷查询    /mem  /disk  /uptime  /ip
```

**高能操作：**

| 你发的 | 效果 |
|--------|------|
| `/status` | 一键看服务器全貌 |
| `/run apt install xxx` | 手机上装软件 |
| `/run docker compose up -d` | 手机上部署服务 |
| `/restart nginx` | 重启任何服务 |
| `/run tmux kill-session -t claude` | 硬杀 Claude |
| `/run reboot` | 重启服务器 |

---

## Webhook — 让监控系统喂消息给 Claude

```bash
# 你的监控脚本检测到异常：
curl -X POST http://localhost:8900/api/message \
  -H "Content-Type: application/json" \
  -d '{"token":"secret","text":"🚨 nginx 挂了","source":"monitor"}'

# Claude 主会话立刻收到，开始自动修复
```

<details>
<summary>📦 完整监控脚本示例（点击展开）</summary>

**Nginx 健康检查：**
```bash
#!/bin/bash
# cron: * * * * *
CODE=$(curl -s -o /dev/null -w "%{http_code}" https://yoursite.com)
[ "$CODE" != "200" ] && curl -X POST localhost:8900/api/message \
  -H "Content-Type: application/json" \
  -d "{\"token\":\"secret\",\"text\":\"🚨 网站异常 HTTP $CODE\"}"
```

**SSL 到期提醒：**
```bash
#!/bin/bash
# cron: 0 9 * * *
DAYS=$(echo | openssl s_client -connect yoursite.com:443 2>/dev/null | openssl x509 -noout -enddate | cut -d= -f2)
DAYS_LEFT=$(( ($(date -d "$DAYS" +%s) - $(date +%s)) / 86400 ))
[ "$DAYS_LEFT" -lt 7 ] && curl -X POST localhost:8900/api/message \
  -H "Content-Type: application/json" \
  -d "{\"token\":\"secret\",\"text\":\"⚠️ SSL 证书还有 ${DAYS_LEFT} 天到期\"}"
```

**Docker 容器监控：**
```bash
#!/bin/bash
# cron: */5 * * * *
for c in myapp postgres redis; do
  S=$(docker inspect -f '{{.State.Status}}' $c 2>/dev/null)
  [ "$S" != "running" ] && curl -X POST localhost:8900/api/message \
    -H "Content-Type: application/json" \
    -d "{\"token\":\"secret\",\"text\":\"🚨 容器 $c 状态: $S\"}"
done
```

**磁盘告警：**
```bash
#!/bin/bash
# cron: 0 * * * *
U=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
[ "$U" -gt 85 ] && curl -X POST localhost:8900/api/message \
  -H "Content-Type: application/json" \
  -d "{\"token\":\"secret\",\"text\":\"⚠️ 磁盘 ${U}%\"}"
```

</details>

---

## 技术细节

| 组件 | 技术 |
|------|------|
| 核心 | Python 3.10+, asyncio |
| HTTP | httpx (异步) |
| 语音识别 | faster-whisper (medium, int8, CPU) |
| 语音合成 | edge-tts (微软, 免费) |
| 音频 | ffmpeg |
| 微信图片 | AES-ECB 解密 |
| 部署 | systemd + tmux |

**文件清单（就这么几个）：**

```
poller.py          ← 核心，800 行搞定一切
reply.py           ← 回复 + 跨平台广播
stream_reply.py    ← 流式回复（打字机效果）
msg_store.py       ← 消息存储
claude_status.py   ← 状态监控
menu.py            ← 菜单系统
install.sh         ← 一键安装
```

---

## 对比

| | 派派 | cc-connect | cc-weixin |
|---|---|---|---|
| 消息直达 Claude 主会话 | ✅ | ❌ 桥接 | ❌ 桥接 |
| 菜单硬控（重启/命令） | ✅ | 部分 | ❌ |
| Webhook 告警 + 自动修复 | ✅ | ❌ | ❌ |
| 跨平台广播 | ✅ | ❌ | ❌ |
| 语音对话 | ✅ | ❌ | ❌ |
| 代码量 | ~800 行 | 大型项目 | 中型项目 |
| API 费用 | $0 | 按量计费 | 按量计费 |

---

**派派只做一件事：把你的消息送到 Claude 面前。剩下的，Claude 搞定。**

## License

MIT
