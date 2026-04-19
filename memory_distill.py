#!/usr/bin/env python3
"""
派派记忆蒸馏 — 用 claude -p 为每个 raw 模块生成摘要/标签/实体

流程:
  1. SELECT * FROM modules WHERE status='raw' ORDER BY start_ts LIMIT N
  2. 读原始 jsonl（本地优先，不存再从 R2 拉）
  3. 截断到前 20K chars 避免 token 爆炸
  4. 喂给 claude -p 强制输出 JSON
  5. 更新 modules 表，status → distilled

Usage:
  python3 memory_distill.py              # 处理 raw 模块 默认 5 个
  python3 memory_distill.py --limit 20   # 处理 20 个
  python3 memory_distill.py --id <id>    # 处理指定 module
  python3 memory_distill.py --all        # 一直处理直到无 raw
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, '/root/paipai')
from memory_db import get_conn
from r2_vault import get_client, _load_env

_load_env()

RAW_BUCKET = os.environ.get('R2_BUCKET_ARCHIVE', 'paipai-archive')

DISTILL_PROMPT = """你是记忆系统的蒸馏器。下面是一段 Claude Code 会话或派派消息的原始 JSONL。你要提炼出结构化元数据。

严格返回 JSON 格式（没有其他文字，没有 markdown 代码块包裹），字段：
{
  "title": "20 字内的模块标题，一眼看出做了什么",
  "category": "从这些选一个 → investment / infra / chitchat / debug / research / paipai-setup / other",
  "summary": "200 字内的精华摘要，侧重决策和产出",
  "tags": ["3-6 个关键词标签"],
  "entities": ["涉及的股票代码/公司/项目/人名，如 RKLB, AMZN, paipai-digest"]
}

JSONL 原文（已截断）:
{raw_text}
"""


def _load_raw(module: dict, s3) -> str:
    """Load raw jsonl content — try local path first, fall back to R2."""
    session_id = module['id']
    # Look for local Claude session file
    local = f'/root/.claude/projects/-home-ristonburras/{session_id}.jsonl'
    if os.path.exists(local):
        return Path(local).read_text(errors='replace')
    # Fall back to R2
    ptr = module['r2_pointer'] or ''
    if ptr.startswith('s3://'):
        key = ptr.split('/', 3)[-1]
        bucket = ptr.split('/')[2]
        try:
            return s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8', errors='replace')
        except Exception as e:
            print(f'⚠️ cannot load R2 {ptr}: {e}')
    return ''


def _extract_text_from_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get('type') == 'text':
                    parts.append(b.get('text', ''))
                elif b.get('type') == 'tool_use':
                    parts.append(f'[tool:{b.get("name","?")}]')
                elif b.get('type') == 'tool_result':
                    # Skip (too noisy)
                    pass
        return '\n'.join(parts)
    return ''


def _compact_jsonl(raw: str, max_chars: int = 18000) -> str:
    """Extract user/assistant text from Claude Code session jsonl.

    Schema (Claude Code v2.x): each line has `type` and nested `message` dict.
    """
    # Also support paipai inbox format: {source, text, ...}
    is_inbox = '"source":"wx"' in raw[:500] or '"source":"tg"' in raw[:500]
    out = []
    for line in raw.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if is_inbox:
            text = e.get('text') or ''
            reply = e.get('reply') or ''
            text = text.strip()
            if text:
                out.append(f'U: {text[:600]}')
            if reply and reply != '(cleared)':
                out.append(f'A: {reply[:600]}')
        else:
            etype = e.get('type')
            if etype not in ('user', 'assistant'):
                continue
            msg = e.get('message', {})
            if not isinstance(msg, dict):
                continue
            content = msg.get('content', '')
            text = _extract_text_from_content(content).strip()
            if not text:
                continue
            prefix = 'U:' if etype == 'user' else 'A:'
            out.append(f'{prefix} {text[:600]}')
        if sum(len(s) for s in out) > max_chars:
            break
    return '\n'.join(out)[:max_chars]


def _call_claude(prompt: str, timeout: int = 120) -> str:
    """Invoke claude -p, return stdout text only (strip interceptor noise)."""
    try:
        proc = subprocess.run(
            ['claude', '-p', '--output-format', 'text'],
            input=prompt, capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ''
    out = proc.stdout or ''
    # Strip intercept banner lines
    lines = [l for l in out.splitlines() if not l.startswith('[ic v3]')]
    return '\n'.join(lines).strip()


def _extract_json(text: str) -> dict:
    """Find first {..} JSON blob."""
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return {}
    blob = m.group(0)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # Try fixup: remove markdown code fences
        blob = re.sub(r'^```(?:json)?\s*', '', blob).rstrip('` \n')
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            return {}


def distill_module(conn, module: dict, s3) -> bool:
    raw = _load_raw(module, s3)
    if not raw:
        print(f'  ⚠️ {module["id"][:8]} no raw content, skip')
        return False
    compact = _compact_jsonl(raw)
    if not compact:
        print(f'  ⚠️ {module["id"][:8]} no text extracted, skip')
        return False
    prompt = DISTILL_PROMPT.replace('{raw_text}', compact)
    t0 = time.time()
    resp = _call_claude(prompt)
    dt = time.time() - t0
    data = _extract_json(resp)
    if not data.get('title'):
        print(f'  ❌ {module["id"][:8]} invalid response after {dt:.1f}s')
        return False

    tags = ','.join(data.get('tags', [])) if isinstance(data.get('tags'), list) else str(data.get('tags', ''))
    entities = json.dumps(data.get('entities', []), ensure_ascii=False)
    conn.execute("""
        UPDATE modules SET
          title = ?, category = ?, summary = ?, tags = ?, entities = ?,
          token_spent = ?, status = 'distilled', updated_at = strftime('%s','now')
        WHERE id = ?
    """, (
        data.get('title', '')[:80],
        data.get('category', 'other'),
        data.get('summary', '')[:1000],
        tags[:200],
        entities[:400],
        len(compact) // 4,  # rough token estimate
        module['id'],
    ))
    print(f'  ✅ {module["id"][:8]} [{data.get("category","?")}] {data.get("title","")[:40]} ({dt:.1f}s)')
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=5)
    ap.add_argument('--id')
    ap.add_argument('--all', action='store_true')
    args = ap.parse_args()

    conn = get_conn()
    s3 = get_client()

    if args.id:
        cur = conn.execute('SELECT * FROM modules WHERE id = ?', (args.id,))
        mods = [dict(r) for r in cur.fetchall()]
    else:
        cur = conn.execute(
            "SELECT * FROM modules WHERE status = 'raw' ORDER BY start_ts DESC "
            f"LIMIT {'99999' if args.all else int(args.limit)}"
        )
        mods = [dict(r) for r in cur.fetchall()]

    if not mods:
        print('(no raw modules)')
        return

    print(f'distilling {len(mods)} module(s)')
    ok = 0
    for m in mods:
        if distill_module(conn, m, s3):
            ok += 1
    conn.commit()
    print(f'\ndone: {ok}/{len(mods)} distilled')


if __name__ == '__main__':
    main()
