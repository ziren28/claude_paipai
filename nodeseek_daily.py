#!/usr/bin/env python3
"""
NodeSeek 每日任务 — 签到 + 浏览热帖
通过 CDP 操控容器内 Chromium 执行
"""
import json
import urllib.request
import time
import websocket
import sys

CDP_URL = "http://127.0.0.1:9222"


def get_ws():
    """Get WebSocket connection to the active page tab."""
    # Run inside docker
    tabs = json.loads(urllib.request.urlopen(f"{CDP_URL}/json").read())
    page = [t for t in tabs if t["type"] == "page"][0]
    return websocket.create_connection(page["webSocketDebuggerUrl"], timeout=15)


def cdp(ws, method, params=None):
    msg_id = int(time.time() * 1000) % 100000
    r = {"id": msg_id, "method": method}
    if params:
        r["params"] = params
    ws.send(json.dumps(r))
    for _ in range(50):
        d = json.loads(ws.recv())
        if d.get("id") == msg_id:
            return d
    return None


def js(ws, expression, await_promise=False):
    params = {"expression": expression}
    if await_promise:
        params["awaitPromise"] = True
    r = cdp(ws, "Runtime.evaluate", params)
    return r.get("result", {}).get("result", {}).get("value", "")


def sign_in(ws):
    """Execute daily sign-in."""
    # Navigate to NodeSeek
    cdp(ws, "Page.navigate", {"url": "https://www.nodeseek.com"})
    time.sleep(3)

    # Check login
    title = js(ws, "document.title")
    if "登录" in title:
        return {"success": False, "message": "未登录，需要手动登录"}

    # Try random sign-in (chance for bonus)
    result = js(ws, '''
(async()=>{
  try{
    var r = await fetch("/api/attendance?random=true", {method:"POST", credentials:"include"});
    var d = await r.json();
    return JSON.stringify(d);
  }catch(e){return JSON.stringify({success:false,message:e.message})}
})()
''', await_promise=True)

    try:
        return json.loads(result)
    except:
        return {"success": False, "message": result}


def browse_hot_posts(ws, count=5):
    """Browse hot posts to increase activity."""
    cdp(ws, "Page.navigate", {"url": "https://www.nodeseek.com"})
    time.sleep(3)

    # Get post links
    links = js(ws, f'''
Array.from(document.querySelectorAll("a[href*='/post-']"))
    .map(a => a.href)
    .filter((v,i,a) => a.indexOf(v)===i)
    .slice(0, {count})
    .join("\\n")
''')

    visited = []
    for link in links.strip().splitlines():
        if not link:
            continue
        cdp(ws, "Page.navigate", {"url": link})
        time.sleep(2)
        title = js(ws, "document.title")
        visited.append(title)

    return visited


def get_summary(ws):
    """Get account summary."""
    cdp(ws, "Page.navigate", {"url": "https://www.nodeseek.com"})
    time.sleep(3)

    info = js(ws, '''
(async()=>{
  try{
    var r = await fetch("/api/attendance", {credentials:"include"});
    var d = await r.json();
    return JSON.stringify(d);
  }catch(e){return JSON.stringify({error:e.message})}
})()
''', await_promise=True)

    try:
        return json.loads(info)
    except:
        return {"raw": info}


def main():
    ws = get_ws()
    results = []

    # 1. Sign in
    print("=== 签到 ===")
    r = sign_in(ws)
    print(json.dumps(r, ensure_ascii=False))
    results.append(f"签到: {r.get('message', str(r))}")

    # 2. Browse posts
    print("\n=== 浏览热帖 ===")
    posts = browse_hot_posts(ws, 5)
    for p in posts:
        print(f"  visited: {p}")
    results.append(f"浏览: {len(posts)} 篇帖子")

    # 3. Summary
    print("\n=== 账户状态 ===")
    s = get_summary(ws)
    print(json.dumps(s, ensure_ascii=False))

    ws.close()

    # Return summary
    summary = " | ".join(results)
    print(f"\n总结: {summary}")
    return summary


if __name__ == "__main__":
    main()
