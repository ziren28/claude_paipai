#!/usr/bin/env python3
"""
派派记忆检索 — FTS5 关键词匹配 × 时间权重衰减

得分公式：
  score = fts_rank * exp(-lambda * days_ago)
  lambda 默认 0.05（20 天半衰期）

Usage:
  python3 memory_search.py "RKLB 估值"
  python3 memory_search.py "段永平" --limit 10
  python3 memory_search.py --category investment
  python3 memory_search.py --json "R2 灾备"  # 机器可读
"""
import argparse
import json
import math
import sys
import time
from datetime import datetime

sys.path.insert(0, '/root/paipai')
from memory_db import get_conn


DEFAULT_LAMBDA = 0.05   # per day — 20 days half-life


def search(query: str = None, category: str = None, limit: int = 10,
           lambda_: float = DEFAULT_LAMBDA) -> list:
    conn = get_conn()
    now = time.time()
    if query:
        # LIKE-based search (CJK-friendly, v1 fallback since FTS5 unicode61 doesn't tokenize Chinese well)
        terms = [t.strip() for t in query.split() if t.strip()]
        where_parts = []
        params = []
        for t in terms:
            like = f'%{t}%'
            where_parts.append(
                '(title LIKE ? OR summary LIKE ? OR tags LIKE ? OR entities LIKE ?)'
            )
            params.extend([like, like, like, like])
        sql = ("SELECT id, source, start_ts, category, title, summary, tags, entities, r2_pointer, "
               f"{len(terms)} as fts_rank FROM modules WHERE ({' AND '.join(where_parts) or '1=1'})")
        if category:
            sql += ' AND category = ?'
            params.append(category)
        sql += ' LIMIT 100'
        cur = conn.execute(sql, params)
    else:
        # No query, just filter by category + time
        sql = "SELECT id, source, start_ts, category, title, summary, tags, entities, r2_pointer, 0.0 as fts_rank FROM modules"
        cond = []; params = []
        if category:
            cond.append('category = ?'); params.append(category)
        if cond:
            sql += ' WHERE ' + ' AND '.join(cond)
        sql += ' ORDER BY start_ts DESC LIMIT 50'
        cur = conn.execute(sql, params)

    results = []
    for row in cur.fetchall():
        days_ago = (now - row['start_ts']) / 86400
        time_w = math.exp(-lambda_ * max(days_ago, 0))
        # fts_rank is negative (lower = better in SQLite FTS5 bm25)
        # LIKE mode: fts_rank stores term match count; higher = more matches
        hit_bonus = float(row['fts_rank'] or 0)
        score = (hit_bonus + 0.5) * time_w
        results.append({
            'id': row['id'],
            'source': row['source'],
            'date': datetime.fromtimestamp(row['start_ts']).strftime('%Y-%m-%d %H:%M'),
            'category': row['category'],
            'title': row['title'],
            'summary': row['summary'],
            'tags': row['tags'],
            'entities': row['entities'],
            'pointer': row['r2_pointer'],
            'score': round(score, 3),
            'days_ago': round(days_ago, 1),
        })
    conn.close()
    results.sort(key=lambda r: r['score'], reverse=True)
    return results[:limit]


def format_result(r: dict) -> str:
    lines = [
        f'⚡ {r["score"]}  📅 {r["date"]} ({r["days_ago"]}d ago)  [{r["category"] or "?"}]',
        f'   {r["title"]}',
    ]
    if r['summary']:
        lines.append(f'   💬 {r["summary"][:140]}')
    if r['tags']:
        lines.append(f'   🏷️ {r["tags"]}')
    lines.append(f'   → {r["id"][:16]}')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('query', nargs='?', default=None)
    ap.add_argument('--category')
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    results = search(args.query, args.category, args.limit)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif not results:
        print('(no match)')
    else:
        for r in results:
            print(format_result(r))
            print()


if __name__ == '__main__':
    main()
