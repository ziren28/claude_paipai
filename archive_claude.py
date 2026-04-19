#!/usr/bin/env python3
"""
派派数字孪生备份 — 每天把 /root/.claude/{projects,skills} 打成 tar.gz 上 R2

内容：
  /root/.claude/projects/  → 会话历史 + memory/ 记忆
  /root/.claude/skills/    → 段永平+巴菲特+穷查理 skill 库

跳过：
  cache/ paste-cache/ session-env/ telemetry/ shell-snapshots/ backups/
  plugins/（可从源头重装，单独打包太大）

.credentials.json / settings.json 另走 sync.py 实时同步，不在这里重复。
"""
import io
import os
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, '/root/paipai')
from r2_vault import get_client, _load_env

_load_env()

CLAUDE_DIR = '/root/.claude'
INCLUDE = ['projects', 'skills']
SKIP_NAMES = {'__pycache__', '.cache', 'node_modules'}

ARCHIVE_BUCKET = os.environ.get('R2_BUCKET_ARCHIVE', 'paipai-archive')


def tar_filter(tarinfo):
    """Exclude cache-like files."""
    name = os.path.basename(tarinfo.name)
    if name in SKIP_NAMES:
        return None
    return tarinfo


def build_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz', compresslevel=6) as tar:
        for sub in INCLUDE:
            p = os.path.join(CLAUDE_DIR, sub)
            if os.path.exists(p):
                tar.add(p, arcname=sub, filter=tar_filter)
    return buf.getvalue()


def main():
    now = datetime.now(timezone.utc)
    data = build_tarball()
    key = f'claude/{now:%Y-%m-%d}.tar.gz'
    get_client().put_object(
        Bucket=ARCHIVE_BUCKET, Key=key, Body=data,
        ContentType='application/gzip',
    )
    print(f'✅ claude 备份 → {ARCHIVE_BUCKET}/{key} ({len(data):,} B gzipped)')


if __name__ == '__main__':
    main()
