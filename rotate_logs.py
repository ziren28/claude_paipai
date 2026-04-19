#!/usr/bin/env python3
"""
派派日志轮转 — 每天 00:10 UTC 滚存日志到 R2 archive，本机清空

处理日志：
  /root/paipai/poller.log     → r2://paipai-archive/logs/poller/YYYY-MM-DD.log.gz
  /root/paipai/digest.log     → r2://paipai-archive/logs/digest/YYYY-MM-DD.log.gz
  /root/paipai/sync.log       → r2://paipai-archive/logs/sync/YYYY-MM-DD.log.gz
  /root/paipai/archive.log    → r2://paipai-archive/logs/archive/YYYY-MM-DD.log.gz

流程：
  1. 读全文件 → gzip 压缩 → 上传 R2
  2. 截断本机文件（truncate 避免重启服务）
  3. 只保留最近 1 天在本机
"""
import gzip
import io
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, '/root/paipai')
from r2_vault import get_client, _load_env

_load_env()

LOGS = {
    '/root/paipai/poller.log': 'poller',
    '/root/paipai/digest.log': 'digest',
    '/root/paipai/sync.log': 'sync',
    '/root/paipai/archive.log': 'archive',
}
ARCHIVE_BUCKET = os.environ.get('R2_BUCKET_ARCHIVE', 'paipai-archive')


def rotate_file(local_path: str, label: str, target_date: datetime):
    p = Path(local_path)
    if not p.exists() or p.stat().st_size == 0:
        print(f'(skip empty: {local_path})')
        return 0

    data = p.read_bytes()
    # Compress with gzip
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6) as gz:
        gz.write(data)
    compressed = buf.getvalue()

    key = f'logs/{label}/{target_date:%Y-%m-%d}.log.gz'
    get_client().put_object(
        Bucket=ARCHIVE_BUCKET, Key=key, Body=compressed,
        ContentType='application/gzip',
    )
    # Truncate local (safer than delete: services keeping file handle continue writing)
    p.write_bytes(b'')
    print(f'✅ {label}: {len(data)} B → {len(compressed)} B gz → {ARCHIVE_BUCKET}/{key}')
    return len(compressed)


def main():
    target = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    total = 0
    for path, label in LOGS.items():
        try:
            total += rotate_file(path, label, target)
        except Exception as e:
            print(f'❌ {path}: {type(e).__name__}: {e}')
    print(f'\ntotal uploaded: {total} B compressed')


if __name__ == '__main__':
    main()
