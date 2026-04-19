#!/usr/bin/env bash
#
# 派派一键安装/恢复脚本
#
# 交互式: bash <(curl -fsSL https://raw.githubusercontent.com/ziren28/claude_paipai/main/install.sh)
# 静默式: curl -fsSL ... | R2_ACCOUNT_ID=xxx R2_ACCESS_KEY_ID=xxx R2_SECRET_ACCESS_KEY=xxx bash -s -- --silent
# 参数式: bash install.sh --account-id xxx --access-key xxx --secret-key xxx
#
# 流程：
#   1. 装系统依赖（python3 git curl pip）
#   2. 装 Python 包（boto3 httpx）
#   3. git clone 代码到 /root/paipai/
#   4. 拿到 R2 凭证 → 写 .env
#   5. bootstrap.py 从 R2 拉所有 secrets + Claude 凭证
#   6. 拉最新 .claude 快照 tar.gz 解压
#   7. 安装 systemd 单元 + 启动服务
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ziren28/claude_paipai.git}"
PAIPAI_DIR="${PAIPAI_DIR:-/root/paipai}"
SILENT=0

# ----- 解析参数 -----
ACCOUNT_ID="${R2_ACCOUNT_ID:-${CLOUDFLARE_ACCOUNT_ID:-}}"
ACCESS_KEY="${R2_ACCESS_KEY_ID:-}"
SECRET_KEY="${R2_SECRET_ACCESS_KEY:-}"
SKIP_SYSTEMD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --silent) SILENT=1; shift ;;
    --account-id) ACCOUNT_ID="$2"; shift 2 ;;
    --access-key) ACCESS_KEY="$2"; shift 2 ;;
    --secret-key) SECRET_KEY="$2"; shift 2 ;;
    --skip-systemd) SKIP_SYSTEMD=1; shift ;;
    --help|-h)
      head -n 15 "$0" | tail -n 13
      exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

# ----- 辅助函数 -----
log()  { printf '\n\033[1;36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
ok()   { printf '\033[1;32m✅ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m⚠️  %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m❌ %s\033[0m\n' "$*" >&2; exit 1; }

prompt_if_empty() {
  local var_name="$1" prompt="$2"
  if [[ -z "${!var_name}" && $SILENT -eq 0 ]]; then
    read -r -p "$prompt: " val
    printf -v "$var_name" '%s' "$val"
  fi
  [[ -n "${!var_name}" ]] || die "$var_name 未提供（可用 --$(echo "$var_name" | tr 'A-Z_' 'a-z-') 或 env 传入）"
}

# ----- Step 1: 系统依赖 -----
log "1/7 安装系统依赖..."
if command -v apt-get >/dev/null; then
  apt-get update -qq >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-pip git curl tar gzip >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q python3 python3-pip git curl tar gzip
else
  die "不支持的包管理器（需要 apt-get 或 dnf）"
fi
ok "系统依赖就绪"

# ----- Step 2: Python 包 -----
log "2/7 安装 Python 包..."
pip install -q --break-system-packages boto3 httpx python-dotenv 2>/dev/null || \
  pip install -q boto3 httpx python-dotenv
ok "Python 依赖就绪"

# ----- Step 3: git clone -----
log "3/7 Clone 代码仓库..."
if [[ -d "$PAIPAI_DIR/.git" ]]; then
  warn "$PAIPAI_DIR 已存在，git pull 更新"
  (cd "$PAIPAI_DIR" && git pull -q)
else
  git clone -q "$REPO_URL" "$PAIPAI_DIR"
fi
ok "代码就绪于 $PAIPAI_DIR"

# ----- Step 4: R2 凭证 -----
log "4/7 配置 R2 主凭证..."
prompt_if_empty ACCOUNT_ID "Cloudflare Account ID (32 位 hex)"
prompt_if_empty ACCESS_KEY "R2 Access Key ID"
prompt_if_empty SECRET_KEY "R2 Secret Access Key"

cat > "$PAIPAI_DIR/.env" <<EOF
CLOUDFLARE_ACCOUNT_ID=$ACCOUNT_ID
R2_ACCESS_KEY_ID=$ACCESS_KEY
R2_SECRET_ACCESS_KEY=$SECRET_KEY
R2_ENDPOINT=https://$ACCOUNT_ID.r2.cloudflarestorage.com
R2_BUCKET_MEDIA=paipai-media
R2_BUCKET_ARCHIVE=paipai-archive
R2_BUCKET_SECRETS=paipai-secrets
EOF
chmod 600 "$PAIPAI_DIR/.env"
ok ".env 已写（mode 600）"

# ----- Step 5: bootstrap -----
log "5/7 从 R2 拉取所有凭证..."
(cd "$PAIPAI_DIR" && python3 bootstrap.py) || die "bootstrap 失败"

# ----- Step 6: Claude 数字孪生恢复 -----
log "6/7 拉取 .claude 数字孪生快照..."
mkdir -p /root/.claude
python3 - <<PYEOF
import sys; sys.path.insert(0, "$PAIPAI_DIR")
from r2_vault import list_bucket, download_file
import os
snaps = [o for o in list_bucket("paipai-archive", "claude/") if o["key"].endswith(".tar.gz")]
if not snaps:
    print("⚠️ R2 里没有 claude 快照（首次安装很正常）")
else:
    latest = max(snaps, key=lambda o: o["modified"])
    print(f'最新快照: {latest["key"]} ({latest["size"]:,} B)')
    download_file("paipai-archive", latest["key"], "/tmp/claude_snapshot.tar.gz")
    os.system("tar xzf /tmp/claude_snapshot.tar.gz -C /root/.claude/")
    os.unlink("/tmp/claude_snapshot.tar.gz")
    print("✅ 解压完成")
PYEOF

# ----- Step 7: systemd 单元（可选） -----
if [[ $SKIP_SYSTEMD -eq 1 ]]; then
  warn "跳过 systemd 安装 (--skip-systemd)"
elif ! command -v systemctl >/dev/null || ! [[ -d /run/systemd/system ]]; then
  warn "systemd 不可用（容器/WSL 环境？），跳过 systemd 安装"
  echo "  手动启动： cd $PAIPAI_DIR && python3 poller.py &"
else
  log "7/7 安装 systemd 单元..."
  cp "$PAIPAI_DIR"/systemd/*.service /etc/systemd/system/ 2>/dev/null || true
  cp "$PAIPAI_DIR"/systemd/*.timer /etc/systemd/system/ 2>/dev/null || true
  systemctl daemon-reload
  systemctl enable --now inbox-poller.service 2>/dev/null || warn "inbox-poller 启动失败，检查 systemctl status"
  for t in paipai-sync paipai-digest paipai-dailyhot paipai-archive-daily paipai-archive-claude paipai-rotate-logs; do
    systemctl enable --now ${t}.timer 2>/dev/null || systemctl enable --now ${t}.service 2>/dev/null || true
  done
  ok "systemd 就位"
fi

# ----- 总结 -----
PY_COUNT=$(ls "$PAIPAI_DIR"/*.py 2>/dev/null | wc -l)
SYSTEMD_COUNT=$(ls "$PAIPAI_DIR"/systemd/ 2>/dev/null | wc -l)
ENV_COUNT=$(grep -c '=' "$PAIPAI_DIR/.env" 2>/dev/null || echo 0)
CREDS_OK=$([ -f /root/.claude/.credentials.json ] && echo OK || echo 缺失)
SKILL_COUNT=$([ -d /root/.claude/skills ] && ls /root/.claude/skills | wc -l || echo 0)
SESSION_COUNT=$(find /root/.claude/projects -name '*.jsonl' 2>/dev/null | wc -l)

cat <<SUMMARY

═══════════════════════════════════════════════
🎉 派派恢复完成
═══════════════════════════════════════════════
位置: $PAIPAI_DIR
代码: $PY_COUNT python 脚本 + $SYSTEMD_COUNT systemd 单元
环境: $ENV_COUNT 个变量在 .env
Claude 凭证: $CREDS_OK
Skill: $SKILL_COUNT 个
会话: $SESSION_COUNT 条历史

🚀 下一步:
  验证: curl http://127.0.0.1:8900/api/health
  状态: systemctl status inbox-poller --no-pager
═══════════════════════════════════════════════
SUMMARY
