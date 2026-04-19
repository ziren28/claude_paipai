#!/usr/bin/env python3
"""
派派资讯聚合推送 — 每 30 分钟汇总新闻 / 投资 / 羊毛 推给 Max WX

Sources:
- 新闻: 36Kr newsflash, IT之家 RSS, 少数派 RSS
- 社区: V2EX hot
- 投资: Coingecko trending + BTC/ETH/SOL 行情
- 羊毛: V2EX hot 过滤优惠关键词

Dedup: /root/paipai/.digest_seen.json (保留最近 500 个 item id)

Usage:
  python3 digest.py              # 发送一次
  python3 digest.py --dry        # 打印，不发送
"""
import json
import os
import re
import sys
import time
import html
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import httpx

WX_STATE_FILE = os.environ.get("WX_STATE_FILE", "/root/paipai/wechat/state.json")
SEEN_FILE = "/root/paipai/.digest_seen.json"
TRANSLATE_CACHE_FILE = "/root/paipai/.digest_translations.json"
SEEN_TTL_SEC = 6 * 3600  # 6h: topics older than this can resurface
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

# --- translate (Google unofficial) ---

_translate_cache: dict = {}

def _load_tr_cache():
    global _translate_cache
    try:
        _translate_cache = json.loads(Path(TRANSLATE_CACHE_FILE).read_text())
    except Exception:
        _translate_cache = {}

def _save_tr_cache():
    # Bound to last 1000 entries
    keys = list(_translate_cache.keys())[-1000:]
    data = {k: _translate_cache[k] for k in keys}
    try:
        Path(TRANSLATE_CACHE_FILE).write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass

def translate_en_zh(client, text: str) -> str:
    """Translate English → 简体中文 via Google unofficial endpoint. Cached."""
    if not text or not text.strip():
        return text
    key = text[:500]
    if key in _translate_cache:
        return _translate_cache[key]
    try:
        r = client.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "zh-CN",
                    "dt": "t", "q": text[:1500]},
            timeout=8,
        )
        d = r.json()
        zh = "".join(s[0] for s in (d[0] or []) if s and s[0])
        zh = zh.strip()
        if zh:
            _translate_cache[key] = zh
            return zh
    except Exception as e:
        print(f"translate fail: {e}", file=sys.stderr)
    return text

# --- dedup state: {id: first_seen_ts}  ---

def load_seen() -> dict:
    try:
        raw = json.loads(Path(SEEN_FILE).read_text())
        # legacy: old format was a list[str]; migrate silently
        if isinstance(raw, list):
            now = time.time()
            return {i: now for i in raw}
        return {k: float(v) for k, v in raw.items()}
    except Exception:
        return {}

def prune_seen(seen: dict) -> dict:
    """Drop entries older than TTL."""
    cutoff = time.time() - SEEN_TTL_SEC
    return {k: v for k, v in seen.items() if v >= cutoff}

def save_seen(seen: dict):
    seen = prune_seen(seen)
    Path(SEEN_FILE).write_text(json.dumps(seen, ensure_ascii=False))


# --- fetchers: each returns list of dicts {id, cat, title, url, extra} ---

DAILYHOT_BASE = "http://127.0.0.1:6688"

def fetch_dailyhot(client, source: str, cat: str, prefix: str = "",
                   limit: int = 10, id_prefix: str = None) -> list:
    """Generic fetcher for local DailyHotApi instance."""
    try:
        r = client.get(f"{DAILYHOT_BASE}/{source}", timeout=10)
        d = r.json()
        if d.get("code") != 200:
            return []
        out = []
        for it in d.get("data", [])[:limit]:
            title = (it.get("title") or "").strip()
            if not title:
                continue
            item_id = str(it.get("id") or it.get("url") or title[:30])
            out.append({
                "id": f"{id_prefix or source}:{item_id}",
                "cat": cat,
                "title": f"{prefix}{title}",
                "url": it.get("url") or it.get("mobileUrl") or "",
            })
        return out
    except Exception as e:
        print(f"dailyhot/{source} fail: {e}", file=sys.stderr)
        return []

def fetch_v2ex(client) -> list:
    try:
        r = client.get("https://www.v2ex.com/api/topics/hot.json", timeout=8)
        items = r.json()
        return [{
            "id": f"v2ex:{i['id']}",
            "cat": "community",
            "title": f"[{i['node']['title']}] {i['title']}",
            "url": i["url"],
        } for i in items[:15]]
    except Exception as e:
        print(f"v2ex fail: {e}", file=sys.stderr)
        return []

def fetch_ithome(client) -> list:
    try:
        r = client.get("https://www.ithome.com/rss/",
                       headers={"User-Agent": UA}, timeout=8)
        # Titles may or may not be CDATA-wrapped; tolerate both
        items = re.findall(
            r"<item>\s*<title>(?:<!\[CDATA\[(.*?)\]\]>|(.*?))</title>.*?<link>\s*(.*?)\s*</link>",
            r.text, re.S
        )
        out = []
        for cdata, plain, link in items[:20]:
            title = (cdata or plain).strip()
            if not title:
                continue
            link = link.strip()
            uid = re.search(r"/(\d+)\.htm", link)
            out.append({
                "id": f"ithome:{uid.group(1) if uid else link}",
                "cat": "news",
                "title": title,
                "url": link,
            })
        return out
    except Exception as e:
        print(f"ithome fail: {e}", file=sys.stderr)
        return []

def fetch_musk(client) -> list:
    """Elon Musk tweets via xcancel.com (nitter fork). Translates to zh-CN."""
    try:
        r = client.get("https://xcancel.com/elonmusk",
                       headers={"User-Agent": UA}, timeout=10)
        items = re.findall(
            r"<a class=\"tweet-link\"[^>]+href=\"(/elonmusk/status/\d+[^\"]*)\"[^>]*>.*?"
            r"<div class=\"tweet-content media-body\"[^>]*>(.*?)</div>",
            r.text, re.S
        )
        out = []
        for href, content in items[:5]:
            clean = re.sub(r"<[^>]+>", " ", content)
            clean = html.unescape(re.sub(r"\s+", " ", clean)).strip()
            if not clean or len(clean) < 3:
                continue
            sid = re.search(r"/status/(\d+)", href).group(1)
            zh = translate_en_zh(client, clean[:300])
            # Show Chinese primarily; append (EN) only if translation clearly differs
            title = f"🚀 Musk: {zh[:140]}"
            if zh != clean and len(clean) < 80:
                title += f"\n   🔤 {clean[:80]}"
            out.append({
                "id": f"musk:{sid}",
                "cat": "twitter",
                "title": title,
                "url": f"https://x.com/elonmusk/status/{sid}",
            })
        return out
    except Exception as e:
        print(f"musk fail: {e}", file=sys.stderr)
        return []

def fetch_trump(client) -> list:
    """Trump's Truth Social via trumpstruth.org RSS. Translates to zh-CN."""
    try:
        r = client.get("https://trumpstruth.org/feed",
                       headers={"User-Agent": UA}, timeout=10)
        items = re.findall(
            r"<item>.*?<title>(?:<!\[CDATA\[(.*?)\]\]>|(.*?))</title>.*?"
            r"<link>\s*(.*?)\s*</link>",
            r.text, re.S
        )
        out = []
        for cd, pl, link in items[:5]:
            title_en = html.unescape((cd or pl).strip())
            link = link.strip()
            if title_en.startswith("[No Title]"):
                continue
            sid = re.search(r"/statuses/(\d+)", link)
            sid = sid.group(1) if sid else link
            zh = translate_en_zh(client, title_en[:300])
            title = f"🇺🇸 Trump: {zh[:140]}"
            if zh != title_en and len(title_en) < 80:
                title += f"\n   🔤 {title_en[:80]}"
            out.append({
                "id": f"trump:{sid}",
                "cat": "twitter",
                "title": title,
                "url": link,
            })
        return out
    except Exception as e:
        print(f"trump fail: {e}", file=sys.stderr)
        return []

def fetch_hackernews(client) -> list:
    """HackerNews top stories — translates title to zh-CN."""
    try:
        r = client.get("https://hacker-news.firebaseio.com/v0/topstories.json",
                       timeout=8)
        ids = r.json()[:15]
        out = []
        for hid in ids:
            try:
                d = client.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{hid}.json",
                    timeout=4,
                ).json()
                if not d or d.get("type") != "story":
                    continue
                title_en = d.get("title", "")
                zh = translate_en_zh(client, title_en)
                out.append({
                    "id": f"hn:{hid}",
                    "cat": "news",
                    "title": f"🌐 {zh} ({d.get('score',0)}pt)",
                    "url": d.get("url") or f"https://news.ycombinator.com/item?id={hid}",
                })
                if len(out) >= 5:
                    break
            except Exception:
                continue
        return out
    except Exception as e:
        print(f"hn fail: {e}", file=sys.stderr)
        return []

def fetch_sspai(client) -> list:
    try:
        r = client.get("https://sspai.com/feed",
                       headers={"User-Agent": UA}, timeout=8)
        items = re.findall(
            r"<item>\s*<title>(.*?)</title>.*?<link>(.*?)</link>",
            r.text, re.S
        )
        out = []
        for t, l in items[:10]:
            t = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", t).strip()
            uid = re.search(r"/post/(\d+)", l)
            out.append({
                "id": f"sspai:{uid.group(1) if uid else l}",
                "cat": "news",
                "title": t,
                "url": l.strip(),
            })
        return out
    except Exception as e:
        print(f"sspai fail: {e}", file=sys.stderr)
        return []

def fetch_crypto(client) -> list:
    """Coingecko trending + 主流币行情。总是最新，不走 dedup。"""
    out = []
    try:
        r = client.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,ethereum,solana,binancecoin"
            "&vs_currencies=usd&include_24hr_change=true",
            timeout=8,
        )
        d = r.json()
        prices = []
        for coin, key in [("bitcoin", "BTC"), ("ethereum", "ETH"),
                          ("solana", "SOL"), ("binancecoin", "BNB")]:
            if coin in d:
                price = d[coin]["usd"]
                chg = d[coin].get("usd_24h_change", 0)
                arrow = "↑" if chg >= 0 else "↓"
                prices.append(f"{key} ${price:,.0f} {arrow}{abs(chg):.1f}%")
        if prices:
            out.append({
                "id": f"crypto_price:{int(time.time() // 1800)}",
                "cat": "invest",
                "title": " | ".join(prices),
                "url": "https://www.coingecko.com",
                "always": True,  # 行情每次都推
            })
    except Exception as e:
        print(f"crypto price fail: {e}", file=sys.stderr)

    try:
        r = client.get("https://api.coingecko.com/api/v3/search/trending",
                       timeout=8)
        d = r.json()
        for c in d.get("coins", [])[:5]:
            i = c["item"]
            chg = i.get("data", {}).get("price_change_percentage_24h", {}).get("usd", 0)
            arrow = "↑" if chg >= 0 else "↓"
            out.append({
                "id": f"trending:{i['id']}",
                "cat": "invest",
                "title": f"🔥 {i['name']} ({i['symbol']}) rank #{i.get('market_cap_rank') or '?'} {arrow}{abs(chg):.1f}%",
                "url": f"https://www.coingecko.com/en/coins/{i['id']}",
            })
    except Exception as e:
        print(f"trending fail: {e}", file=sys.stderr)
    return out


# --- 羊毛 过滤: V2EX 全站标题含关键词 ---
YANGMAO_KEYWORDS = ["免费", "白嫖", "优惠", "折扣", "活动", "领取",
                    "红包", "0 元", "0元", "0.01", "薅羊毛", "福利",
                    "送", "限免", "特价", "满减", "赠送", "补贴",
                    "返利", "抽奖", "免单", "秒杀", "活期", "体验金"]

# Yangmao-related V2EX nodes (strict: 优惠/赠送/拼单)
YANGMAO_NODES = {"promotions", "deals", "freebie", "invite"}

def fetch_yangmao(client) -> list:
    """从 V2EX 全站近期话题里过滤羊毛：节点白名单 OR 关键词命中。"""
    try:
        r = client.get("https://www.v2ex.com/api/topics/latest.json",
                       headers={"User-Agent": UA}, timeout=8)
        items = r.json()
        out = []
        for i in items[:80]:
            t = i.get("title", "")
            node = i.get("node", {}).get("name", "")
            if node in YANGMAO_NODES or any(kw in t for kw in YANGMAO_KEYWORDS):
                out.append({
                    "id": f"v2ex_ym:{i['id']}",
                    "cat": "yangmao",
                    "title": f"[{i['node']['title']}] {t}",
                    "url": i["url"],
                })
                if len(out) >= 10:
                    break
        return out
    except Exception as e:
        print(f"yangmao fail: {e}", file=sys.stderr)
        return []


# --- main aggregation ---

def gather_all() -> list:
    with httpx.Client(follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=6) as ex:
            fs = [
                ex.submit(fetch_v2ex, client),
                ex.submit(fetch_ithome, client),
                ex.submit(fetch_sspai, client),
                ex.submit(fetch_hackernews, client),
                ex.submit(fetch_crypto, client),
                ex.submit(fetch_yangmao, client),
                ex.submit(fetch_musk, client),
                ex.submit(fetch_trump, client),
                # DailyHotApi aggregators
                ex.submit(fetch_dailyhot, client, "nodeseek",    "yangmao",   "🟢 NS: ", 8),
                ex.submit(fetch_dailyhot, client, "hostloc",     "yangmao",   "🛠️ HL: ", 8),
                ex.submit(fetch_dailyhot, client, "linuxdo",     "community", "🐧 LD: ", 6),
                ex.submit(fetch_dailyhot, client, "weibo",       "news",      "📱 微博: ", 10),
                ex.submit(fetch_dailyhot, client, "zhihu",       "news",      "❓ 知乎: ", 8),
                ex.submit(fetch_dailyhot, client, "bilibili",    "community", "📺 B站: ", 6),
                ex.submit(fetch_dailyhot, client, "douban-movie","community", "🎬 豆瓣: ", 5),
                ex.submit(fetch_dailyhot, client, "hellogithub", "news",      "🌈 HG: ", 5),
                ex.submit(fetch_dailyhot, client, "huxiu",       "news",      "🐯 虎嗅: ", 6),
                ex.submit(fetch_dailyhot, client, "36kr",        "news",      "🚀 36Kr: ", 6),
                ex.submit(fetch_dailyhot, client, "juejin",      "community", "💎 掘金: ", 4),
            ]
            all_items = []
            for f in fs:
                try:
                    all_items.extend(f.result(timeout=15))
                except Exception as e:
                    print(f"fetcher failed: {type(e).__name__}: {e}", file=sys.stderr)
            return all_items


def format_digest(fresh: list) -> str:
    """按分类组织并格式化。"""
    buckets = {"invest": [], "twitter": [], "yangmao": [],
               "news": [], "community": []}
    for it in fresh:
        buckets.setdefault(it["cat"], []).append(it)

    ts = time.strftime("%m-%d %H:%M")
    total = sum(len(v) for v in buckets.values())
    lines = [f"📡 派派资讯 · {ts}  ({total} 条)"]

    if buckets["invest"]:
        lines.append("\n💰 投资行情")
        for it in buckets["invest"][:10]:
            lines.append(f"• {it['title']}")

    if buckets["twitter"]:
        lines.append("\n🐦 推特")
        for it in buckets["twitter"][:8]:
            lines.append(f"• {it['title'][:140]}")
            lines.append(f"  {it['url']}")

    if buckets["yangmao"]:
        lines.append("\n🐑 羊毛 / VPS / 优惠")
        for it in buckets["yangmao"][:15]:
            lines.append(f"• {it['title'][:70]}")
            if it.get("url"):
                lines.append(f"  {it['url']}")

    if buckets["news"]:
        lines.append("\n📰 新闻 / 热点")
        for it in buckets["news"][:25]:
            title = it['title'][:70]
            if it.get("url"):
                lines.append(f"• {title}")
                lines.append(f"  {it['url']}")
            else:
                lines.append(f"• {title}")

    if buckets["community"]:
        lines.append("\n💬 社区 / 讨论")
        for it in buckets["community"][:15]:
            lines.append(f"• {it['title'][:70]}")
            if it.get("url"):
                lines.append(f"  {it['url']}")

    return "\n".join(lines)


def send_wx(text: str):
    state = json.loads(Path(WX_STATE_FILE).read_text())
    token = state["bot_token"]
    base = state["base_url"].rstrip("/")
    to = state["owner_user_id"]
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "AuthorizationType": "ilink_bot_token",
    }
    with httpx.Client() as c:
        # Split into <= 1500-char chunks (WX-friendly)
        import uuid
        for i in range(0, len(text), 1500):
            chunk = text[i:i+1500]
            body = {"msg": {
                "from_user_id": "",
                "to_user_id": to,
                "client_id": f"digest-{uuid.uuid4().hex[:10]}",
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": chunk}}],
            }}
            r = c.post(f"{base}/ilink/bot/sendmessage",
                       json=body, headers=headers, timeout=15)
            print(f"WX sent: {r.status_code}")


def _normalize_title(title: str) -> str:
    """Normalize title for cross-source dedup: strip category/node prefix, punct."""
    for p in ("🟢 NS: ", "🛠️ HL: ", "🐧 LD: ", "📱 微博: ", "❓ 知乎: ",
             "📺 B站: ", "🎬 豆瓣: ", "🌈 HG: ", "🐯 虎嗅: ", "🚀 36Kr: ",
             "💎 掘金: ", "🌐 ", "🚀 Musk: ", "🇺🇸 Trump: ", "🔥 "):
        if title.startswith(p):
            title = title[len(p):]
            break
    title = re.sub(r"^\[[^\]]+\]\s*", "", title)
    keep = re.sub(r"[^\w\u4e00-\u9fff]+", "", title).lower()
    return keep[:40]


def main():
    dry = "--dry" in sys.argv
    _load_tr_cache()
    seen = prune_seen(load_seen())
    items = gather_all()

    fresh = []
    non_always_ids = []
    title_sigs_seen = set()  # within-batch: drop cross-source same-title dupes
    for it in items:
        if it.get("always"):
            fresh.append(it)
            continue
        if it["id"] in seen:
            continue
        sig = _normalize_title(it.get("title", ""))
        if sig and sig in title_sigs_seen:
            continue  # another source already reported the same story
        title_sig_id = f"sig:{sig}"
        if sig and title_sig_id in seen:
            continue  # same story reported earlier (different source id)
        title_sigs_seen.add(sig)
        fresh.append(it)
        non_always_ids.append(it["id"])
        if sig:
            non_always_ids.append(title_sig_id)

    if not fresh:
        print("(没有新内容，跳过)")
        return

    digest = format_digest(fresh)
    print(digest)
    print(f"\n共 {len(fresh)} 条 (含 {sum(1 for f in fresh if f.get('always'))} 条常推; seen={len(seen)})")

    if dry:
        return

    send_wx(digest)
    now = time.time()
    for nid in non_always_ids:
        seen[nid] = now
    save_seen(seen)
    _save_tr_cache()


if __name__ == "__main__":
    main()
