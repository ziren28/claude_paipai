#!/usr/bin/env python3
"""
微信扫码绑定工具 — 通过 iLink Bot API 获取二维码，扫码后自动写入 state.json
基于 @tencent-weixin/openclaw-weixin 插件协议逆向实现

用法:
    python3 wx_qr_bind.py                       # 交互式绑定，state 写入默认路径
    python3 wx_qr_bind.py --state /path/to/state.json  # 指定 state 文件路径
    python3 wx_qr_bind.py --env                  # 同时更新 .env 中的 WX_STATE_FILE
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    print("❌ 缺少 httpx，请先: pip install httpx")
    sys.exit(1)

# ── 常量 ────────────────────────────────────────────────────────────────────

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
BOT_TYPE = "3"  # iLink Bot 类型，与官方插件一致
QR_POLL_TIMEOUT = 35  # 长轮询超时（秒）
QR_EXPIRE_MAX_REFRESH = 3  # 二维码过期后最多刷新次数
LOGIN_TIMEOUT = 300  # 总登录超时（秒）

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_STATE_DIR = SCRIPT_DIR / "wechat"
DEFAULT_STATE_FILE = DEFAULT_STATE_DIR / "state.json"

# ── 颜色 ────────────────────────────────────────────────────────────────────

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log(msg):
    print(f"{CYAN}[wx-bind]{RESET} {msg}")


def log_ok(msg):
    print(f"{GREEN}[wx-bind]{RESET} {msg}")


def log_warn(msg):
    print(f"{YELLOW}[wx-bind]{RESET} {msg}")


def log_err(msg):
    print(f"{RED}[wx-bind]{RESET} {msg}", file=sys.stderr)


# ── API 调用 ─────────────────────────────────────────────────────────────────

def fetch_qr_code(client: httpx.Client) -> dict:
    """请求 iLink API 获取登录二维码"""
    url = f"{ILINK_BASE_URL}/ilink/bot/get_bot_qrcode?bot_type={BOT_TYPE}"
    resp = client.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def poll_qr_status(client: httpx.Client, qrcode: str, base_url: str = ILINK_BASE_URL) -> dict:
    """长轮询二维码扫描状态"""
    url = f"{base_url}/ilink/bot/get_qrcode_status?qrcode={qrcode}"
    try:
        resp = client.get(url, timeout=QR_POLL_TIMEOUT + 5)
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        return {"status": "wait"}
    except Exception:
        return {"status": "wait"}


# ── 终端二维码 ───────────────────────────────────────────────────────────────

def print_qr_terminal(url: str):
    """尝试在终端打印二维码，失败则只显示链接"""
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=1, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
        return
    except ImportError:
        pass

    # 备选：用 Unicode block 字符手绘（需要 qrcode 库）
    log_warn("提示: pip install qrcode 可在终端直接显示二维码")
    print()
    print(f"  {BOLD}请在浏览器中打开以下链接，用微信扫码:{RESET}")
    print(f"  {CYAN}{url}{RESET}")
    print()


# ── 主流程 ───────────────────────────────────────────────────────────────────

def do_bind(state_file: Path, update_env: bool = False):
    print()
    print(f"{CYAN}╔══════════════════════════════════════════╗{RESET}")
    print(f"{CYAN}║  微信扫码绑定 — 派派 WeChat Binding      ║{RESET}")
    print(f"{CYAN}╚══════════════════════════════════════════╝{RESET}")
    print()

    log("正在请求登录二维码...")

    with httpx.Client() as client:
        # 1. 获取二维码
        try:
            qr_data = fetch_qr_code(client)
        except Exception as e:
            log_err(f"获取二维码失败: {e}")
            sys.exit(1)

        qrcode_token = qr_data.get("qrcode", "")
        qrcode_url = qr_data.get("qrcode_img_content", "")

        if not qrcode_token or not qrcode_url:
            log_err(f"二维码响应异常: {json.dumps(qr_data, ensure_ascii=False)}")
            sys.exit(1)

        log_ok("二维码已获取！请使用微信扫描:")
        print()
        print_qr_terminal(qrcode_url)
        print(f"  {BOLD}二维码链接:{RESET} {qrcode_url}")
        print()

        # 2. 轮询扫码状态
        deadline = time.time() + LOGIN_TIMEOUT
        scanned_printed = False
        refresh_count = 1
        current_base_url = ILINK_BASE_URL

        while time.time() < deadline:
            status_data = poll_qr_status(client, qrcode_token, current_base_url)
            status = status_data.get("status", "wait")

            if status == "wait":
                # 静默等待
                continue

            elif status == "scaned":
                if not scanned_printed:
                    log("👀 已扫码，请在微信上确认...")
                    scanned_printed = True

            elif status == "expired":
                refresh_count += 1
                if refresh_count > QR_EXPIRE_MAX_REFRESH:
                    log_err("二维码多次过期，请重新运行脚本")
                    sys.exit(1)

                log_warn(f"二维码已过期，正在刷新... ({refresh_count}/{QR_EXPIRE_MAX_REFRESH})")
                try:
                    qr_data = fetch_qr_code(client)
                    qrcode_token = qr_data["qrcode"]
                    qrcode_url = qr_data["qrcode_img_content"]
                    scanned_printed = False
                    current_base_url = ILINK_BASE_URL
                    print()
                    print_qr_terminal(qrcode_url)
                    print(f"  {BOLD}新二维码链接:{RESET} {qrcode_url}")
                    print()
                except Exception as e:
                    log_err(f"刷新二维码失败: {e}")
                    sys.exit(1)

            elif status == "scaned_but_redirect":
                redirect_host = status_data.get("redirect_host", "")
                if redirect_host:
                    current_base_url = f"https://{redirect_host}"
                    log(f"IDC 重定向: {redirect_host}")

            elif status == "confirmed":
                bot_token = status_data.get("bot_token", "")
                bot_id = status_data.get("ilink_bot_id", "")
                base_url = status_data.get("baseurl", ILINK_BASE_URL)
                user_id = status_data.get("ilink_user_id", "")

                if not bot_token:
                    log_err("登录确认但未返回 bot_token，请重试")
                    sys.exit(1)

                # 3. 写入 state.json
                state_file.parent.mkdir(parents=True, exist_ok=True)

                # 如果已有 state.json，保留 get_updates_buf
                old_buf = ""
                if state_file.exists():
                    try:
                        old_state = json.loads(state_file.read_text())
                        old_buf = old_state.get("get_updates_buf", "")
                    except Exception:
                        pass

                state = {
                    "bot_token": bot_token,
                    "base_url": base_url or ILINK_BASE_URL,
                    "get_updates_buf": old_buf,
                    "owner_user_id": user_id,
                    "ilink_bot_id": bot_id,
                    "bound_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                state_file.write_text(
                    json.dumps(state, ensure_ascii=False, indent=2)
                )
                os.chmod(state_file, 0o600)

                print()
                log_ok("✅ 微信绑定成功！")
                print()
                print(f"  Bot ID:       {bot_id}")
                print(f"  User ID:      {user_id}")
                print(f"  Base URL:     {base_url or ILINK_BASE_URL}")
                print(f"  Token:        {bot_token[:12]}...{bot_token[-4:]}")
                print(f"  State 文件:   {state_file}")
                print()

                # 4. 可选：更新 .env
                if update_env:
                    env_file = SCRIPT_DIR / ".env"
                    _update_env_file(env_file, str(state_file))

                # 5. 提示重启
                print(f"{YELLOW}下一步:{RESET}")
                print(f"  systemctl restart inbox-poller   # 重启派派以加载新配置")
                print()
                return

        log_err("登录超时，请重新运行脚本")
        sys.exit(1)


def _update_env_file(env_file: Path, state_path: str):
    """更新 .env 文件中的 WX_STATE_FILE"""
    if not env_file.exists():
        log_warn(f".env 文件不存在: {env_file}，跳过更新")
        return

    lines = env_file.read_text().splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.startswith("WX_STATE_FILE="):
            new_lines.append(f"WX_STATE_FILE={state_path}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"WX_STATE_FILE={state_path}")

    env_file.write_text("\n".join(new_lines) + "\n")
    log_ok(f".env 已更新: WX_STATE_FILE={state_path}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="微信扫码绑定工具 — 派派 WeChat Binding"
    )
    parser.add_argument(
        "--state", type=str, default=None,
        help=f"state.json 路径 (默认: {DEFAULT_STATE_FILE})"
    )
    parser.add_argument(
        "--env", action="store_true",
        help="同时更新 .env 中的 WX_STATE_FILE"
    )
    args = parser.parse_args()

    state_file = Path(args.state) if args.state else DEFAULT_STATE_FILE
    do_bind(state_file, update_env=args.env)


if __name__ == "__main__":
    main()
