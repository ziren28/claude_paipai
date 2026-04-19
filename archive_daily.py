#!/usr/bin/env python3
"""
派派日归档 — 每天 00:05 把昨天的 messages 滚存到 R2 archive bucket

流程：
  1. 从 /root/inbox/messages.jsonl 提取昨天（当地时区 UTC）的所有消息
  2. 上传到 r2://paipai-archive/messages/YYYY/MM/YYYY-MM-DD.jsonl
  3. 可选：本机保留（append-only 哲学）— 这里保留，R2 仅做灾备

未来扩展：滚存 poller.log / digest.log
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, '/root/paipai')
from r2_vault import get_client, _load_env

_load_env()

INBOX = '/root/inbox/messages.jsonl'
ARCHIVE_BUCKET = os.environ.get('R2_BUCKET_ARCHIVE', 'paipai-archive')


def archive_day(target_date: datetime):
    """Extract messages with ts on target_date (UTC), upload to R2."""
    start_ts = target_date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    end_ts = start_ts + 86400

    picked = []
    with open(INBOX) as f:
        for line in f:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = m.get('ts', 0)
            if start_ts <= ts < end_ts:
                picked.append(line)

    if not picked:
        print(f'(no messages on {target_date.date()}, skip)')
        return 0

    key = f'messages/{target_date:%Y}/{target_date:%m}/{target_date:%Y-%m-%d}.jsonl'
    data = ''.join(picked).encode('utf-8')
    client = get_client()
    client.put_object(
        Bucket=ARCHIVE_BUCKET, Key=key, Body=data,
        ContentType='application/x-ndjson',
    )
    print(f'✅ archived {len(picked)} messages → {ARCHIVE_BUCKET}/{key} ({len(data)} B)')
    return len(picked)


def main():
    # Default: archive yesterday (UTC)
    if len(sys.argv) > 1:
        target = datetime.strptime(sys.argv[1], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    else:
        target = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
    archive_day(target)


if __name__ == '__main__':
    main()
