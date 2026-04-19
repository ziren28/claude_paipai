# 派派灾备手册

服务器被毁 / 换机 / 整理搬迁时，这份文档告诉你如何一小时内让派派从零恢复。

## 信任链

```
Cloudflare 账号 (email + 2FA)
    │  登录 dashboard 生成
    ▼
R2 API Token (Access Key ID + Secret Access Key)
    │  写入本机 /root/paipai/.env
    ▼
r2://paipai-secrets/ 保险库
    │  通过 bootstrap.py 下载
    ▼
所有其他凭证（TG / WX / Anthropic / ...）
```

**根信任 = Cloudflare 账号**。只要能登陆 Cloudflare，一切都能重建。

## 完整恢复流程

### 1. 准备新机（Linux，任意发行版）

```bash
apt update && apt install -y python3 python3-pip git tmux systemd
pip install boto3 httpx aiohttp cryptography edge-tts faster-whisper \
            --break-system-packages
```

### 2. 克隆代码（公开仓库）

```bash
cd /root
git clone https://github.com/ziren28/claude_paipai.git paipai
cd paipai
```

仓库里**不含任何 secret**，只有代码 + systemd 单元 + 本手册。

### 3. 生成 R2 API Token

打开 https://dash.cloudflare.com → R2 → Overview → 右上 **Manage R2 API Tokens** → **Create API Token**

- Permissions: **Admin Read & Write**
- Buckets: **Apply to all buckets**
- TTL: 推荐 90 天（设日历提醒到期换）

复制输出的三个值：
- `Access Key ID` (32 字符)
- `Secret Access Key` (64 字符)  
- `Jurisdiction` (通常 `default`)

另外去 Dashboard 首页右下角记 `Account ID` (32 位 hex)。

### 4. 写入 .env

```bash
cat > /root/paipai/.env <<EOF
CLOUDFLARE_ACCOUNT_ID=你的_account_id
R2_ACCESS_KEY_ID=你的_access_key_id
R2_SECRET_ACCESS_KEY=你的_secret_access_key
R2_ENDPOINT=https://你的_account_id.r2.cloudflarestorage.com
R2_BUCKET_MEDIA=paipai-media
R2_BUCKET_ARCHIVE=paipai-archive
R2_BUCKET_SECRETS=paipai-secrets
EOF
chmod 600 /root/paipai/.env
```

### 5. 跑 bootstrap

```bash
cd /root/paipai
python3 bootstrap.py
```

输出应为：
```
manifest v1 · 2 files
✅ secrets.env → /root/paipai/.env.secrets (mode 0o600)
✅ wechat/state.json → /root/paipai/wechat/state.json (mode 0o600)
✅ merged N vars into .env
```

### 6. 安装 systemd 服务

```bash
cp /root/paipai/systemd/*.service /etc/systemd/system/
cp /root/paipai/systemd/*.timer /etc/systemd/system/ 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now inbox-poller paipai-digest.timer paipai-dailyhot
```

（如果缺 DailyHot：进 `/root/paipai/tools/dailyhot` 跑 `npm ci && npm run build`。）

### 7. 验证

```bash
systemctl status inbox-poller --no-pager -l
curl -s http://127.0.0.1:8900/api/health
python3 /root/paipai/reply.py --list
```

派派恢复完成，继续干活。

## Token 轮换

### R2 Token 定期轮换（推荐 90 天）

1. Dashboard → R2 → Manage API Tokens → 生成新 token
2. 跑 `python3 rotate_r2.py`，按提示输入新凭证
3. 脚本验证新 token 能读 paipai-secrets → 更新本机 .env → Dashboard 撤销旧 token
4. 下次服务重启自动用新 token

### 其他凭证轮换

因为都存在 R2 secrets bucket，只需：
1. 源头平台生成新凭证（TG → BotFather, WX → 重扫码, Anthropic → 控制台）
2. 更新 R2：`python3 r2_vault.py put paipai-secrets secrets.env /root/paipai/.env.new`
3. 下次 bootstrap 自动同步到本机

## 应急 Break Glass

如果 R2 Token 丢了且没来得及备份：

1. 登 Cloudflare Dashboard 生成新 Token（Account 2FA 是最后防线）
2. 回到"完整恢复流程"第 4 步继续

如果 Cloudflare 账号丢了（2FA 设备坏 + 恢复码丢）：

1. 联系 Cloudflare Support 走账号恢复流程
2. 这一条没有技术路径，是纯 **business continuity**

## 所有已知 token 来源

| Token | 生成地 | Regenerate 代价 |
|---|---|---|
| R2 API Token | Cloudflare Dashboard → R2 | 免费，几秒 |
| Cloudflare API Token (cfat_) | Dashboard → Profile → API Tokens | 免费，几秒 |
| TG Bot Token | @BotFather `/revoke` `/token` | 免费，1 分钟 |
| WX bot_token | 重走 `wx_qr_bind.py` 扫码流程 | 免费，扫码 |
| Anthropic API Key | console.anthropic.com/settings/keys | 免费 |
| GitHub PAT | Settings → Developer settings → Tokens | 免费 |

**规则**：凡能免费 regenerate 的，丢了也不慌。真正的根密钥是 Cloudflare 账号本身。

## 版本历史

- 2026-04-19: 初版，基于 Keymaster 架构（R2 为主 token，其他存 R2 secrets）
