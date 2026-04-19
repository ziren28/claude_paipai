#!/usr/bin/env python3
"""
派派股票数据查询 — 为 Claude 讨论具体个股时提供最新数据。

Usage:
  python3 stock.py AMZN            # US stock
  python3 stock.py RKLB
  python3 stock.py 601698          # A-share (auto-detect 6位数字 = SH, 0/3开头 = SZ)
  python3 stock.py sh601698        # A-share 显式
  python3 stock.py 00700           # HK stock (5位数字)
  python3 stock.py AMZN RKLB       # 多股批量
"""
import json
import re
import sys
import time

import httpx

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"


def _fmt_bignum(n: float, currency: str = "") -> str:
    """Format big numbers with 亿 / 万亿 / B / T suffix."""
    if not n:
        return "?"
    abs_n = abs(n)
    if currency == "$":
        if abs_n >= 1e12:
            return f"${n/1e12:.2f}T"
        if abs_n >= 1e9:
            return f"${n/1e9:.2f}B"
        if abs_n >= 1e6:
            return f"${n/1e6:.1f}M"
        return f"${n:,.0f}"
    # CNY / default
    if abs_n >= 1e12:
        return f"{n/1e12:.2f}万亿"
    if abs_n >= 1e8:
        return f"{n/1e8:.2f}亿"
    if abs_n >= 1e4:
        return f"{n/1e4:.1f}万"
    return f"{n:,.0f}"


# ======================== US stocks: Yahoo Finance ========================

def _parse_bignum(s: str) -> float:
    """Parse strings like '48.83B', '1.42T', '575.77M' to float."""
    if not s or s == "n/a":
        return None
    s = s.replace(",", "").replace("$", "").strip()
    mult = 1
    if s.endswith("T"):
        mult = 1e12; s = s[:-1]
    elif s.endswith("B"):
        mult = 1e9; s = s[:-1]
    elif s.endswith("M"):
        mult = 1e6; s = s[:-1]
    elif s.endswith("K"):
        mult = 1e3; s = s[:-1]
    elif s.endswith("%"):
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def fetch_us(client: httpx.Client, symbol: str) -> dict:
    """Yahoo Finance (price) + stockanalysis.com (fundamentals)."""
    # Yahoo chart for real-time price
    chart = client.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        "?interval=1d&range=1d",
        headers={"User-Agent": UA}, timeout=10,
    ).json()
    m = chart["chart"]["result"][0]["meta"]
    price = m["regularMarketPrice"]
    prev = m.get("chartPreviousClose") or m.get("previousClose") or price
    chg_pct = (price / prev - 1) * 100 if prev else 0

    result = {
        "symbol": symbol.upper(),
        "market": "US",
        "price": price,
        "currency": m.get("currency", "USD"),
        "change_pct": chg_pct,
        "prev_close": prev,
        "day_high": m.get("regularMarketDayHigh"),
        "day_low": m.get("regularMarketDayLow"),
        "week52_high": m.get("fiftyTwoWeekHigh"),
        "week52_low": m.get("fiftyTwoWeekLow"),
        "ts": time.time(),
    }

    # stockanalysis.com — fundamentals, no crumb needed
    try:
        info = client.get(
            f"https://stockanalysis.com/api/symbol/s/{symbol.lower()}/info",
            headers={"User-Agent": UA}, timeout=8,
        ).json().get("data", {})
        result["name"] = info.get("nameFull") or info.get("name") or symbol
        q = info.get("quote", {})
        if q.get("h52"):
            result["week52_high"] = q["h52"]
            result["week52_low"] = q["l52"]
    except Exception as e:
        result["_info_err"] = str(e)

    try:
        stats = client.get(
            f"https://stockanalysis.com/api/symbol/s/{symbol.lower()}/statistics",
            headers={"User-Agent": UA}, timeout=8,
        ).json().get("data", {})
        for k, section in stats.items():
            if not isinstance(section, dict):
                continue
            for item in section.get("data", []):
                tid, val = item.get("id"), item.get("value")
                hover = item.get("hover", "")
                if tid == "marketcap":
                    result["market_cap"] = _parse_bignum(hover or val)
                elif tid == "peRatio" or item.get("title") == "PE Ratio":
                    result["pe_ttm"] = _parse_bignum(val)
                elif tid == "psRatio" or item.get("title") == "PS Ratio":
                    result["ps_ttm"] = _parse_bignum(val)
                elif item.get("title") == "Forward PE":
                    result["pe_fwd"] = _parse_bignum(val)
                elif item.get("title") == "Analyst Consensus":
                    result["analyst_consensus"] = val
                elif item.get("title") == "Price Target":
                    result["target_price"] = _parse_bignum(val)
                elif item.get("title") == "Analyst Count":
                    result["analyst_count"] = _parse_bignum(val)
                elif item.get("title") == "Revenue":
                    result["revenue_ttm"] = _parse_bignum(val)
                elif item.get("title") == "Net Income":
                    result["net_income_ttm"] = _parse_bignum(val)
                elif item.get("title") == "Free Cash Flow":
                    result["fcf_ttm"] = _parse_bignum(val)
                elif item.get("title") == "52-Week Price Change":
                    result["year_change_pct"] = _parse_bignum(val)
    except Exception as e:
        result["_stats_err"] = str(e)

    return result


# ======================== A-shares: 东方财富 ========================

def fetch_a(client: httpx.Client, code: str) -> dict:
    """A-share via 东方财富. code like '601698' or 'sh601698'."""
    # Determine market
    code = code.lower().replace("sh", "").replace("sz", "").lstrip("0") or "0"
    # Simple rule: 6/9 开头 = 沪 (secid=1), 0/3 开头 = 深 (secid=0)
    # 600/601/603/605/688/689 = SH; 00x/30x = SZ
    num = code.zfill(6)
    if num.startswith(("6", "9")):
        secid = f"1.{num}"
    else:
        secid = f"0.{num}"

    # Fields: f43=now*100, f44=high*100, f45=low*100, f46=open*100, f60=prev*100
    # f116=totalMarketCap, f117=流通市值, f162=PE(TTM), f59=精度(小数位), f57=代码, f58=名称
    # f169=change amt, f170=chg pct
    fields = "f43,f44,f45,f46,f57,f58,f59,f60,f116,f117,f162,f167,f169,f170,f177"
    r = client.get(
        f"https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={secid}&fields={fields}",
        headers={"User-Agent": UA}, timeout=8,
    ).json()
    d = r.get("data") or {}
    if not d:
        raise RuntimeError(f"eastmoney returned empty for {code}")

    prec = d.get("f59", 2)
    scale = 10 ** prec
    price = d.get("f43", 0) / scale
    prev = d.get("f60", 0) / scale
    chg_pct = d.get("f170", 0) / 100

    # 52w — fetch separate
    week52 = {"high": None, "low": None}
    try:
        fields52 = "f174,f175"  # 52w high/low
        r52 = client.get(
            f"https://push2.eastmoney.com/api/qt/stock/get"
            f"?secid={secid}&fields={fields52}",
            headers={"User-Agent": UA}, timeout=6,
        ).json()
        d52 = r52.get("data") or {}
        if d52.get("f174") is not None:
            week52["high"] = d52["f174"] / scale
            week52["low"] = d52["f175"] / scale
    except Exception:
        pass

    return {
        "symbol": num,
        "market": "A股",
        "name": d.get("f58", ""),
        "price": price,
        "currency": "CNY",
        "change_pct": chg_pct,
        "prev_close": prev,
        "day_high": d.get("f44", 0) / scale,
        "day_low": d.get("f45", 0) / scale,
        "market_cap": d.get("f116"),
        "float_cap": d.get("f117"),
        "pe_ttm": d.get("f162", 0) / 100 if d.get("f162") else None,
        "week52_high": week52["high"],
        "week52_low": week52["low"],
        "ts": time.time(),
    }


# ======================== HK stocks: 东方财富 港股 ========================

def fetch_hk(client: httpx.Client, code: str) -> dict:
    """HK via 东方财富, secid=116.xxxxx (pad to 5)."""
    num = code.lstrip("0").zfill(5)
    secid = f"116.{num}"
    fields = "f43,f44,f45,f46,f57,f58,f59,f60,f116,f162,f170,f174,f175"
    r = client.get(
        f"https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={secid}&fields={fields}",
        headers={"User-Agent": UA}, timeout=8,
    ).json()
    d = r.get("data") or {}
    if not d:
        raise RuntimeError(f"HK stock {code} not found")
    prec = d.get("f59", 3)
    scale = 10 ** prec
    price = d.get("f43", 0) / scale
    return {
        "symbol": num,
        "market": "港股",
        "name": d.get("f58", ""),
        "price": price,
        "currency": "HKD",
        "change_pct": d.get("f170", 0) / 100,
        "prev_close": d.get("f60", 0) / scale,
        "day_high": d.get("f44", 0) / scale,
        "day_low": d.get("f45", 0) / scale,
        "market_cap": d.get("f116"),
        "pe_ttm": d.get("f162", 0) / 100 if d.get("f162") else None,
        "week52_high": (d.get("f174") or 0) / scale or None,
        "week52_low": (d.get("f175") or 0) / scale or None,
        "ts": time.time(),
    }


# ======================== Dispatch ========================

def detect_market(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith(("SH", "SZ")):
        return "A"
    if re.fullmatch(r"\d{6}", s):
        return "A"
    if re.fullmatch(r"\d{1,5}", s):
        return "HK"  # 1-5 digit likely HK
    if re.fullmatch(r"[A-Z.]+", s):
        return "US"
    return "US"


def fetch(symbol: str) -> dict:
    with httpx.Client(follow_redirects=True) as client:
        market = detect_market(symbol)
        if market == "US":
            return fetch_us(client, symbol.upper())
        if market == "A":
            return fetch_a(client, symbol)
        return fetch_hk(client, symbol)


def format_compact(d: dict) -> str:
    name = d.get("name") or d["symbol"]
    cur_sym = "$" if d["currency"] == "USD" else "HK$" if d["currency"] == "HKD" else "¥"
    chg = d.get("change_pct", 0)
    arrow = "↑" if chg >= 0 else "↓"
    lines = [
        f"📈 {name} ({d['symbol']}) · {d['market']}",
        f"   {cur_sym}{d['price']:.2f}  {arrow}{abs(chg):.2f}%  (昨收 {cur_sym}{d.get('prev_close',0):.2f})",
    ]
    if d.get("day_high") is not None:
        lines.append(f"   日高/低: {cur_sym}{d['day_high']:.2f} / {cur_sym}{d['day_low']:.2f}")
    if d.get("week52_high") is not None:
        lines.append(f"   52周: {cur_sym}{d['week52_high']:.2f} / {cur_sym}{d['week52_low']:.2f}")
    mcap = d.get("market_cap")
    if mcap:
        cur = "$" if d["currency"] == "USD" else ""
        lines.append(f"   市值: {_fmt_bignum(mcap, cur)}")
    fl_cap = d.get("float_cap")
    if fl_cap and fl_cap != mcap:
        lines.append(f"   流通市值: {_fmt_bignum(fl_cap)}")
    pe = d.get("pe_ttm")
    if pe is not None:
        lines.append(f"   PE (TTM): {pe:.1f}" if pe > 0 else f"   PE (TTM): {pe:.1f} (亏损)")
    pe_fwd = d.get("pe_fwd")
    if pe_fwd is not None:
        lines.append(f"   Forward PE: {pe_fwd:.1f}")
    ps = d.get("ps_ttm")
    if ps is not None:
        lines.append(f"   PS (TTM): {ps:.2f}")
    rev = d.get("revenue_ttm")
    if rev:
        lines.append(f"   营收 TTM: {_fmt_bignum(rev, '$' if d['currency']=='USD' else '')}")
    ni = d.get("net_income_ttm")
    if ni is not None:
        lines.append(f"   净利 TTM: {_fmt_bignum(ni, '$' if d['currency']=='USD' else '')}")
    consensus = d.get("analyst_consensus")
    tgt = d.get("target_price")
    count = d.get("analyst_count")
    if consensus and tgt:
        cur_sym2 = "$" if d["currency"] == "USD" else "¥"
        upside = (tgt / d["price"] - 1) * 100 if d.get("price") else 0
        arrow2 = "↑" if upside >= 0 else "↓"
        lines.append(
            f"   分析师({int(count) if count else '?'}人) {consensus}: "
            f"{cur_sym2}{tgt:.2f} {arrow2}{abs(upside):.1f}%"
        )
    yc = d.get("year_change_pct")
    if yc is not None:
        arrow3 = "↑" if yc >= 0 else "↓"
        lines.append(f"   52周涨幅: {arrow3}{abs(yc):.1f}%")
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    args = sys.argv[1:]
    as_json = "--json" in args
    args = [a for a in args if not a.startswith("--")]
    for sym in args:
        try:
            d = fetch(sym)
            if as_json:
                print(json.dumps(d, ensure_ascii=False, indent=2))
            else:
                print(format_compact(d))
                print()
        except Exception as e:
            print(f"❌ {sym}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
