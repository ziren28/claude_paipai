#!/usr/bin/env python3
"""
派派记忆系统 — 摄入现有数据到 L2/L3

数据源：
  1. /root/.claude/projects/-home-ristonburras/*.jsonl  (Claude 会话)
  2. /root/inbox/messages.jsonl                         (派派消息)

流程：
  a. 扫会话文件，每个 session_id 作为一个 module
  b. 提取粗略元数据（时间范围 / 消息数 / 文件大小）
  c. 上传原始 jsonl 到 r2://paipai-archive/raw/sessions/<session_id>.jsonl
  d. 写入 SQLite modules 表（status=raw，等 distill 填 summary/tags）
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, '/root/paipai')
from memory_db import init, get_conn, DB_PATH
from r2_vault import get_client, _load_env

_load_env()

CLAUDE_PROJECTS = '/root/.claude/projects/-home-ristonburras'
INBOX_FILE = '/root/inbox/messages.jsonl'
RAW_BUCKET = os.environ.get('R2_BUCKET_ARCHIVE', 'paipai-archive')


def ingest_claude_session(conn, session_file: Path, s3) -> int:
    """Upload raw jsonl to R2 + index in SQLite."""
    session_id = session_file.stem
    cur = conn.execute('SELECT status FROM modules WHERE id = ?', (session_id,))
    existing = cur.fetchone()
    if existing and existing['status'] in ('distilled', 'folded'):
        return 0  # already fully processed

    # Parse to get start/end ts + first user msg as title hint
    start_ts = end_ts = None
    first_user_text = ''
    event_count = 0
    try:
        with open(session_file) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_count += 1
                ts = e.get('timestamp') or e.get('ts')
                if ts:
                    # ISO or epoch
                    if isinstance(ts, str):
                        from datetime import datetime
                        try:
                            ts = datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
                        except Exception:
                            ts = None
                if ts:
                    if start_ts is None or ts < start_ts:
                        start_ts = ts
                    if end_ts is None or ts > end_ts:
                        end_ts = ts
                if not first_user_text and e.get('role') == 'user':
                    c = e.get('content', '')
                    if isinstance(c, list):
                        for b in c:
                            if isinstance(b, dict) and b.get('type') == 'text':
                                first_user_text = b.get('text', '')[:120]
                                break
                    elif isinstance(c, str):
                        first_user_text = c[:120]
    except Exception as e:
        print(f'⚠️ parse {session_id}: {e}')
        return 0

    if start_ts is None:
        start_ts = session_file.stat().st_mtime
        end_ts = start_ts

    title = (first_user_text or '').strip() or f'session {session_id[:8]}'

    # Upload raw to R2
    r2_key = f'raw/sessions/{session_id}.jsonl'
    try:
        s3.upload_file(
            Filename=str(session_file), Bucket=RAW_BUCKET, Key=r2_key,
            ExtraArgs={'ContentType': 'application/x-ndjson'},
        )
    except Exception as e:
        print(f'❌ R2 upload {session_id}: {e}')
        return 0

    conn.execute("""
        INSERT INTO modules (id, source, start_ts, end_ts, title, r2_pointer, status, updated_at)
        VALUES (?, 'claude_session', ?, ?, ?, ?, 'raw', strftime('%s','now'))
        ON CONFLICT(id) DO UPDATE SET
          end_ts = excluded.end_ts,
          r2_pointer = excluded.r2_pointer,
          updated_at = strftime('%s','now')
    """, (session_id, start_ts, end_ts, title, f's3://{RAW_BUCKET}/{r2_key}'))
    return event_count


def ingest_paipai_inbox(conn, s3) -> int:
    """One module per date = paipai-inbox-YYYY-MM-DD."""
    if not os.path.exists(INBOX_FILE):
        return 0
    import collections
    by_date = collections.defaultdict(list)
    with open(INBOX_FILE) as f:
        for line in f:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = m.get('ts', 0)
            if not ts:
                continue
            from datetime import datetime, timezone
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
            by_date[date_str].append(m)

    count = 0
    for date_str, msgs in by_date.items():
        module_id = f'paipai-inbox-{date_str}'
        cur = conn.execute('SELECT status FROM modules WHERE id = ?', (module_id,))
        if (row := cur.fetchone()) and row['status'] in ('distilled', 'folded'):
            continue
        start_ts = min(m['ts'] for m in msgs)
        end_ts = max(m['ts'] for m in msgs)
        title = f'派派消息 {date_str} ({len(msgs)} 条)'
        r2_key = f'raw/inbox/{date_str}.jsonl'
        body = '\n'.join(json.dumps(m, ensure_ascii=False) for m in msgs).encode()
        try:
            s3.put_object(Bucket=RAW_BUCKET, Key=r2_key, Body=body,
                          ContentType='application/x-ndjson')
        except Exception as e:
            print(f'❌ R2 inbox upload {date_str}: {e}')
            continue
        conn.execute("""
            INSERT INTO modules (id, source, start_ts, end_ts, title, category, r2_pointer, status)
            VALUES (?, 'paipai_inbox', ?, ?, ?, 'messaging', ?, 'raw')
            ON CONFLICT(id) DO UPDATE SET
              end_ts = excluded.end_ts, title = excluded.title,
              updated_at = strftime('%s','now')
        """, (module_id, start_ts, end_ts, title, f's3://{RAW_BUCKET}/{r2_key}'))
        count += len(msgs)
    return count


def main():
    init()
    conn = get_conn()
    s3 = get_client()

    # Claude sessions
    sess_dir = Path(CLAUDE_PROJECTS)
    session_files = sorted(sess_dir.glob('*.jsonl'))
    print(f'found {len(session_files)} Claude session files')
    total_events = 0
    for f in session_files:
        n = ingest_claude_session(conn, f, s3)
        total_events += n
        if n:
            print(f'  ✅ {f.stem[:8]}... {n} events')

    # Paipai inbox
    inbox_n = ingest_paipai_inbox(conn, s3)
    print(f'\npaipai inbox: {inbox_n} events')

    conn.commit()

    # Stats
    cur = conn.execute('SELECT source, status, COUNT(*) FROM modules GROUP BY source, status')
    print('\nmodules by source/status:')
    for row in cur.fetchall():
        print(f'  {row[0]:<16} {row[1]:<12} {row[2]}')
    conn.close()


if __name__ == '__main__':
    main()
