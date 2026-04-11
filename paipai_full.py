#!/usr/bin/env python3
"""
派派 AI — 通过 cc-bridge API 调用 sonnet，不受 root 限制。
支持轻量和全能模式统一入口。
"""
import asyncio
import json
import time
import httpx

BRIDGE_URL = "http://127.0.0.1:5674/v1/messages"
BRIDGE_KEY = "sk-lW66YXGUZMMV7SNbwia6itfev6Fx6p3dzAvLza9oBd4kn0mIj5WfuTQXdye1B"

SYSTEM_PROMPT = (
    "你是派派，Max的AI管家，运行在 cc.maxcole.app 服务器上。"
    "你负责：服务器管理、消息处理、日常协助。"
    "简洁友善，用中文回复，不超过 300 字。"
)


async def send_and_wait(message: str, timeout: int = 60) -> str:
    """Call cc-bridge API with system prompt, parse SSE response."""
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": f"当前时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n{message}"}],
    }

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", BRIDGE_URL,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {BRIDGE_KEY}",
                    "anthropic-version": "2023-06-01",
                },
                timeout=timeout,
            ) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    return f"派派 AI 调用失败 ({resp.status_code})"

                full_text = ""
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            event = json.loads(line[6:])
                            if event.get("type") == "content_block_delta":
                                delta = event.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    full_text += delta.get("text", "")
                        except json.JSONDecodeError:
                            continue

                if not full_text:
                    return "派派 Claude 未返回内容"
                if len(full_text) > 3000:
                    full_text = full_text[:3000] + "\n...(截断)"
                return full_text
    except httpx.TimeoutException:
        return "派派思考超时"
    except Exception as e:
        return f"派派思考出错: {e}"


def send_sync(message: str) -> str:
    return asyncio.run(send_and_wait(message))


if __name__ == "__main__":
    import sys
    msg = " ".join(sys.argv[1:]) or "你好，你是谁"
    print(send_sync(msg))
