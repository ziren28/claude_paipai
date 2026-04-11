#!/bin/bash
#
# 派派 Claude Paipai — 一键安装脚本
# https://github.com/ziren28/claude_paipai
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="${PAIPAI_DIR:-/root/paipai}"

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════╗"
echo "║  派派 Claude Paipai — AI 消息中枢        ║"
echo "║  一键安装脚本                             ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ======================== 1. 检查环境 ========================

echo -e "${YELLOW}[1/7] 检查环境...${NC}"

# Python
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ Python3 未安装${NC}"
    echo "  apt install -y python3 python3-pip"
    exit 1
fi
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "  Python: ${GREEN}${PY_VER}${NC}"

# ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    echo -e "${YELLOW}  ffmpeg 未安装，正在安装...${NC}"
    apt install -y ffmpeg >/dev/null 2>&1
fi
echo -e "  ffmpeg: ${GREEN}$(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)${NC}"

# Claude Code
if command -v claude &>/dev/null; then
    echo -e "  Claude Code: ${GREEN}已安装${NC}"
else
    echo -e "  Claude Code: ${YELLOW}未检测到 (可稍后安装)${NC}"
fi

# tmux
if ! command -v tmux &>/dev/null; then
    echo -e "${YELLOW}  tmux 未安装，正在安装...${NC}"
    apt install -y tmux >/dev/null 2>&1
fi
echo -e "  tmux: ${GREEN}已安装${NC}"

echo ""

# ======================== 2. 安装项目 ========================

echo -e "${YELLOW}[2/7] 安装项目...${NC}"

if [ -d "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/poller.py" ]; then
    echo -e "  目录已存在: ${GREEN}${INSTALL_DIR}${NC}"
    read -p "  覆盖安装? (y/N): " overwrite
    if [[ "$overwrite" != "y" && "$overwrite" != "Y" ]]; then
        echo "  跳过安装，使用现有文件"
    else
        git clone https://github.com/ziren28/claude_paipai.git "$INSTALL_DIR.tmp" 2>/dev/null
        cp -r "$INSTALL_DIR.tmp"/*.py "$INSTALL_DIR.tmp"/*.sh "$INSTALL_DIR.tmp"/.env.example "$INSTALL_DIR/" 2>/dev/null
        rm -rf "$INSTALL_DIR.tmp"
    fi
else
    git clone https://github.com/ziren28/claude_paipai.git "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
echo -e "  安装目录: ${GREEN}${INSTALL_DIR}${NC}"
echo ""

# ======================== 3. 安装依赖 ========================

echo -e "${YELLOW}[3/7] 安装 Python 依赖...${NC}"

pip install --break-system-packages -q httpx cryptography faster-whisper edge-tts 2>/dev/null \
  || pip install -q httpx cryptography faster-whisper edge-tts 2>/dev/null

echo -e "  ${GREEN}依赖安装完成${NC}"

# 预下载 whisper 模型
read -p "  预下载语音识别模型 (~1.5GB)? (Y/n): " dl_whisper
if [[ "$dl_whisper" != "n" && "$dl_whisper" != "N" ]]; then
    echo -e "  ${YELLOW}下载 whisper medium 模型，请稍候...${NC}"
    python3 -c "from faster_whisper import WhisperModel; WhisperModel('medium', device='cpu', compute_type='int8'); print('  ✅ 模型下载完成')"
fi
echo ""

# ======================== 4. 配置 Telegram ========================

echo -e "${YELLOW}[4/7] 配置 Telegram Bot...${NC}"
echo ""
echo -e "  ${CYAN}如何获取 TG Bot Token:${NC}"
echo "  1. 打开 Telegram，搜索 @BotFather"
echo "  2. 发送 /newbot，按提示创建"
echo "  3. 复制 Bot Token (格式: 123456:ABC-DEF...)"
echo ""
echo -e "  ${CYAN}如何获取你的 User ID:${NC}"
echo "  1. 搜索 @userinfobot"
echo "  2. 发送任意消息，它会返回你的 ID (纯数字)"
echo ""

read -p "  TG Bot Token: " TG_TOKEN
read -p "  TG User ID: " TG_OWNER

if [ -z "$TG_TOKEN" ] || [ -z "$TG_OWNER" ]; then
    echo -e "  ${YELLOW}跳过 TG 配置 (稍后编辑 .env)${NC}"
    TG_TOKEN=""
    TG_OWNER="0"
fi
echo ""

# ======================== 5. 配置微信 ========================

echo -e "${YELLOW}[5/7] 配置微信 iLink Bot (可选)...${NC}"
echo ""
echo -e "  ${CYAN}微信 iLink Bot 说明:${NC}"
echo "  微信最新版支持 ClawBot/iLink 接口"
echo "  需要 iOS 8.0.70+ / Android 8.0.69+ / macOS 4.1.8+"
echo ""

read -p "  是否配置微信? (y/N): " setup_wx

WX_STATE_FILE=""
if [[ "$setup_wx" == "y" || "$setup_wx" == "Y" ]]; then
    WX_STATE_DIR="$INSTALL_DIR/wechat"
    mkdir -p "$WX_STATE_DIR"

    echo ""
    echo -e "  ${CYAN}微信登录步骤:${NC}"
    echo "  1. 手机微信 → 设置 → 聊天 → 智能助手 → 开启"
    echo "  2. 创建 Bot 后获取 bot_token"
    echo ""
    read -p "  微信 Bot Token: " WX_BOT_TOKEN
    read -p "  Owner User ID (微信号对应的 iLink ID): " WX_OWNER_ID

    if [ -n "$WX_BOT_TOKEN" ]; then
        cat > "$WX_STATE_DIR/state.json" << WXEOF
{
    "bot_token": "${WX_BOT_TOKEN}",
    "base_url": "https://ilinkai.weixin.qq.com",
    "get_updates_buf": "",
    "owner_user_id": "${WX_OWNER_ID}"
}
WXEOF
        WX_STATE_FILE="$WX_STATE_DIR/state.json"
        echo -e "  ${GREEN}微信配置已保存${NC}"
    fi
fi
echo ""

# ======================== 6. 生成配置文件 ========================

echo -e "${YELLOW}[6/7] 生成配置...${NC}"

# .env
WEBHOOK_SECRET="paipai-$(head -c 8 /dev/urandom | xxd -p)"
cat > "$INSTALL_DIR/.env" << ENVEOF
TG_TOKEN=${TG_TOKEN}
TG_OWNER=${TG_OWNER}
WX_STATE_FILE=${WX_STATE_FILE}
STATUS_FILE=${INSTALL_DIR}/claude_status.json
WEBHOOK_TOKEN=${WEBHOOK_SECRET}
ENVEOF
echo -e "  .env: ${GREEN}已生成${NC}"

# systemd service
cat > /etc/systemd/system/inbox-poller.service << SVCEOF
[Unit]
Description=Claude Paipai Message Hub
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=/usr/bin/python3 poller.py
Restart=always
RestartSec=5
StandardOutput=append:${INSTALL_DIR}/poller.log
StandardError=append:${INSTALL_DIR}/poller.log

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
echo -e "  systemd: ${GREEN}已配置${NC}"

# Claude status monitor
cat > /etc/systemd/system/claude-status.service << CSEOF
[Unit]
Description=Claude Status Monitor
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 claude_status.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
CSEOF
echo -e "  claude-status: ${GREEN}已配置${NC}"

# tmux config
if [ ! -f ~/.tmux.conf ]; then
    cat > ~/.tmux.conf << 'TMUXEOF'
set -g history-limit 50000
set -g mouse on
set -g status-right '#H | %Y-%m-%d %H:%M'
set -g default-terminal "screen-256color"
set -g detach-on-destroy off
TMUXEOF
    echo -e "  tmux.conf: ${GREEN}已生成${NC}"
fi

# Claude 快捷命令
SHELL_RC="$HOME/.bashrc"
[ -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.zshrc"

if ! grep -q "# Claude Paipai aliases" "$SHELL_RC" 2>/dev/null; then
    cat >> "$SHELL_RC" << 'ALIASEOF'

# Claude Paipai aliases
alias cc='claude'
alias cca='claude --dangerously-skip-permissions'
alias ccr='tmux new -s claude "claude --dangerously-skip-permissions" || tmux attach -t claude'
alias pp='python3 /root/paipai/reply.py'
alias pp-list='python3 /root/paipai/reply.py --list'
alias pp-log='tail -f /root/paipai/poller.log'
alias pp-restart='systemctl restart inbox-poller'
alias pp-status='systemctl status inbox-poller --no-pager -l'
ALIASEOF
    echo -e "  快捷命令: ${GREEN}已添加到 ${SHELL_RC}${NC}"
fi

# 创建必要目录
mkdir -p "$INSTALL_DIR/images" "$INSTALL_DIR/files"
echo ""

# ======================== 7. 启动服务 ========================

echo -e "${YELLOW}[7/7] 启动服务...${NC}"

systemctl enable --now claude-status 2>/dev/null
echo -e "  claude-status: ${GREEN}已启动${NC}"

if [ -n "$TG_TOKEN" ] && [ "$TG_TOKEN" != "" ]; then
    systemctl enable --now inbox-poller
    sleep 3
    if systemctl is-active --quiet inbox-poller; then
        echo -e "  inbox-poller: ${GREEN}已启动${NC}"
    else
        echo -e "  inbox-poller: ${RED}启动失败，检查 poller.log${NC}"
    fi
else
    echo -e "  inbox-poller: ${YELLOW}未启动 (需先配置 TG Token)${NC}"
fi

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗"
echo -e "║  ✅ 安装完成！                            ║"
echo -e "╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}快捷命令 (重新打开终端生效):${NC}"
echo "  cc        — 启动 Claude Code"
echo "  cca       — Claude Code 自动模式 (跳过确认)"
echo "  ccr       — tmux 中启动/恢复 Claude (推荐)"
echo "  pp <id> '回复'  — 回复消息"
echo "  pp-list   — 查看待处理消息"
echo "  pp-log    — 实时查看日志"
echo "  pp-restart — 重启派派"
echo "  pp-status — 查看服务状态"
echo ""
echo -e "${GREEN}下一步:${NC}"
echo "  1. source $SHELL_RC     # 立即加载快捷命令"
echo "  2. ccr                   # 启动 Claude Code"
echo "  3. 在 Claude 内输入: 唤醒派派"
echo ""
echo -e "${GREEN}配置文件:${NC}"
echo "  .env        — $INSTALL_DIR/.env"
echo "  日志        — $INSTALL_DIR/poller.log"
echo "  Webhook     — http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_IP'):8900/api/"
echo ""
