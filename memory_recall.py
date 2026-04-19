#!/usr/bin/env python3
"""
派派记忆召回 API — 其他脚本导入调用

典型场景：
  • 派派 poller.py 收到新消息时：先 recall_for_query(msg_text) 看有没有相关历史
  • Claude SessionStart hook：recall_recent_fold() 拿昨日/上周的 fold summary 打底
  • stream_reply.py 生成回复前：融入 recall 结果到 prompt

Usage:
  from memory_recall import recall_for_query, recall_recent_fold, recall_by_category
"""
import sys
from datetime import datetime

sys.path.insert(0, '/root/paipai')
from memory_db import get_conn
from memory_search import search


def recall_for_query(query: str, limit: int = 5) -> list:
    """给定一个查询/消息文本，返回最相关的历史模块摘要列表。"""
    return search(query=query, limit=limit)


def recall_by_category(category: str, days: int = 30, limit: int = 10) -> list:
    """按类别召回近 N 天的模块。"""
    import time
    conn = get_conn()
    cutoff = time.time() - days * 86400
    cur = conn.execute("""
        SELECT id, start_ts, category, title, summary, tags, entities
        FROM modules
        WHERE category = ? AND start_ts > ? AND status IN ('distilled', 'folded')
        ORDER BY start_ts DESC
        LIMIT ?
    """, (category, cutoff, limit))
    rows = []
    for r in cur.fetchall():
        rows.append({
            'id': r['id'],
            'date': datetime.fromtimestamp(r['start_ts']).strftime('%Y-%m-%d'),
            'category': r['category'],
            'title': r['title'],
            'summary': r['summary'],
            'tags': r['tags'],
            'entities': r['entities'],
        })
    conn.close()
    return rows


def recall_recent_fold(level: str = 'daily', count: int = 3) -> list:
    """拉最近 N 个折叠（日/周/月）汇总。"""
    conn = get_conn()
    cur = conn.execute("""
        SELECT id, level, period, module_count, summary, key_events, created_at
        FROM foldings
        WHERE level = ?
        ORDER BY period DESC
        LIMIT ?
    """, (level, count))
    out = []
    for r in cur.fetchall():
        out.append({
            'id': r['id'],
            'period': r['period'],
            'module_count': r['module_count'],
            'summary': r['summary'],
        })
    conn.close()
    return out


def brief_for_session_start() -> str:
    """生成给 Claude SessionStart hook 注入的简报。"""
    daily = recall_recent_fold('daily', 2)
    weekly = recall_recent_fold('weekly', 1)
    monthly = recall_recent_fold('monthly', 1)

    parts = ['📚 派派记忆简报']
    if monthly:
        parts.append(f"\n📅 上月 ({monthly[0]['period']}) — {monthly[0]['module_count']} 个模块")
        parts.append(f"  {monthly[0]['summary'][:300]}")
    if weekly:
        parts.append(f"\n📅 上周 ({weekly[0]['period']}) — {weekly[0]['module_count']} 个模块")
        parts.append(f"  {weekly[0]['summary'][:400]}")
    if daily:
        for d in daily:
            parts.append(f"\n📅 {d['period']} — {d['module_count']} 个模块")
            parts.append(f"  {d['summary'][:500]}")

    if len(parts) == 1:
        return ''  # no fold yet
    return '\n'.join(parts)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p1 = sub.add_parser('query'); p1.add_argument('q')
    p2 = sub.add_parser('category'); p2.add_argument('cat')
    sub.add_parser('brief')
    sub.add_parser('folds')
    args = ap.parse_args()

    if args.cmd == 'query':
        for r in recall_for_query(args.q):
            print(f"• [{r['date']}] {r['title']}")
            print(f"    {r['summary'][:120]}")
    elif args.cmd == 'category':
        for r in recall_by_category(args.cat):
            print(f"• [{r['date']}] {r['title']}")
    elif args.cmd == 'brief':
        print(brief_for_session_start())
    elif args.cmd == 'folds':
        for f in recall_recent_fold('daily', 10):
            print(f"[{f['period']}] {f['module_count']} mods — {f['summary'][:80]}")
