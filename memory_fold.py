#!/usr/bin/env python3
"""
派派记忆折叠 — 日/周/月分级汇总

日级：每天 01:00 UTC 折叠昨天的 modules → foldings 表（level=daily）
周级：每周一 01:10 UTC 折叠上周 7 天日级 → weekly
月级：每月 1 号 01:20 UTC 折叠上月周级 → monthly

输出：一段'当期做了什么'的汇总 + key_events 列表（可点回 modules 查细节）
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, '/root/paipai')
from memory_db import get_conn


FOLD_PROMPT = """你是记忆折叠器。下面是一批已蒸馏的模块元数据，请把它们综合成一段当期摘要。

严格返回 JSON（无 markdown）：
{
  "summary": "300 字内当期主线，侧重'做了什么 / 讨论了什么 / 产出是什么'，按主题分段",
  "key_events": ["最重要的 3-5 个 module_id"],
  "categories_count": {"investment": 3, "infra": 2, ...}
}

期间: {period}
模块列表:
{modules_text}
"""


def get_modules_in_range(conn, start_ts: float, end_ts: float) -> list:
    cur = conn.execute("""
        SELECT id, category, title, summary, tags
        FROM modules
        WHERE start_ts >= ? AND start_ts < ? AND status = 'distilled'
        ORDER BY start_ts ASC
    """, (start_ts, end_ts))
    return [dict(r) for r in cur.fetchall()]


def _call_claude(prompt: str, timeout: int = 120) -> str:
    try:
        proc = subprocess.run(
            ['claude', '-p', '--output-format', 'text'],
            input=prompt, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ''
    out = proc.stdout or ''
    return '\n'.join(l for l in out.splitlines() if not l.startswith('[ic v3]')).strip()


def _extract_json(text: str) -> dict:
    import re
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def fold_period(level: str, period: str, start_ts: float, end_ts: float):
    conn = get_conn()
    fold_id = f'{level}:{period}'

    # Check if already done
    existing = conn.execute(
        'SELECT id FROM foldings WHERE id = ?', (fold_id,),
    ).fetchone()
    if existing:
        print(f'(already folded: {fold_id})')
        conn.close()
        return

    mods = get_modules_in_range(conn, start_ts, end_ts)
    if not mods:
        print(f'(no modules in {period})')
        conn.close()
        return

    # Build modules text (id + title + tags + short summary)
    lines = []
    for m in mods:
        lines.append(f"[{m['category']}] {m['id'][:8]} {m['title'][:50]} — {m['summary'][:80]}")
    modules_text = '\n'.join(lines)

    prompt = FOLD_PROMPT.replace('{period}', period).replace('{modules_text}', modules_text)
    resp = _call_claude(prompt)
    data = _extract_json(resp)

    if not data:
        print(f'❌ claude -p 未返回有效 JSON for {fold_id}')
        conn.close()
        return

    cats_count = data.get('categories_count', {})
    conn.execute("""
        INSERT INTO foldings (id, level, period, module_count, summary, key_events, token_spent)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        fold_id, level, period, len(mods),
        data.get('summary', '')[:3000],
        json.dumps(data.get('key_events', []), ensure_ascii=False),
        len(modules_text) // 4,
    ))

    # Mark folded modules
    for m in mods:
        conn.execute(
            "UPDATE modules SET folded_into = ? WHERE id = ? AND status = 'distilled'",
            (fold_id, m['id']),
        )
    conn.commit()
    conn.close()
    print(f'✅ folded {level}/{period}: {len(mods)} modules, {len(data.get("summary",""))} char summary')


def daily(date_str: str = None):
    if date_str:
        d = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    else:
        d = (datetime.now(timezone.utc) - timedelta(days=1))
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    fold_period('daily', start.strftime('%Y-%m-%d'), start.timestamp(), end.timestamp())


def weekly(week_str: str = None):
    if week_str:
        yr, wk = week_str.split('-W')
        d = datetime.strptime(f'{yr}-W{wk}-1', '%G-W%V-%u').replace(tzinfo=timezone.utc)
    else:
        today = datetime.now(timezone.utc)
        # last week
        d = today - timedelta(days=today.weekday() + 7)
        d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    start = d
    end = d + timedelta(days=7)
    period = f'{start:%G-W%V}'
    fold_period('weekly', period, start.timestamp(), end.timestamp())


def monthly(month_str: str = None):
    if month_str:
        y, m = month_str.split('-')
        d = datetime(int(y), int(m), 1, tzinfo=timezone.utc)
    else:
        today = datetime.now(timezone.utc)
        first = today.replace(day=1)
        d = (first - timedelta(days=1)).replace(day=1)
    start = d
    end = (d.replace(day=28) + timedelta(days=5)).replace(day=1)
    period = f'{start:%Y-%m}'
    fold_period('monthly', period, start.timestamp(), end.timestamp())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('level', choices=['daily', 'weekly', 'monthly'])
    ap.add_argument('--period', help='YYYY-MM-DD / YYYY-WXX / YYYY-MM')
    args = ap.parse_args()
    {'daily': daily, 'weekly': weekly, 'monthly': monthly}[args.level](args.period)


if __name__ == '__main__':
    main()
