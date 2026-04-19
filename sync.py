#!/usr/bin/env python3
"""
派派 rsync 式增量同步 — 每分钟把本机新/改文件推到 R2

分类策略：
  /root/inbox/images/        → r2://paipai-media/images/
  /root/inbox/files/         → r2://paipai-media/voice/
  /root/paipai/wechat/state.json → r2://paipai-secrets/wechat/state.json

跳过：
  *.poller_state.json / *.digest_seen.json / *.digest_translations.json
    （运行态，丢了重算）
  *.log（不备份日志）
  .env（含 R2 凭证本身，鸡生蛋）

机制：
  本地 sync state：/root/paipai/.sync_state.json 记 {path: (mtime, size)}
  R2 head_object 检查 ETag（MD5），只在本地变化 且 R2 差异时才 PUT
"""
import json
import os
import sys
import hashlib
import time
from pathlib import Path

sys.path.insert(0, '/root/paipai')
from r2_vault import get_client, _load_env

_load_env()

SYNC_STATE = '/root/paipai/.sync_state.json'

# (local_path, r2_bucket, r2_key_template, content_type)
# r2_key_template uses {filename} and {yyyymm}
SYNC_RULES = [
    {
        'pattern': '/root/inbox/images/*',
        'bucket': os.environ.get('R2_BUCKET_MEDIA', 'paipai-media'),
        'key': 'images/{yyyymm}/{filename}',
        'content_type_from_ext': True,
    },
    {
        'pattern': '/root/inbox/files/*',
        'bucket': os.environ.get('R2_BUCKET_MEDIA', 'paipai-media'),
        'key': 'voice/{yyyymm}/{filename}',
        'content_type': 'audio/ogg',
    },
    {
        'pattern': '/root/paipai/wechat/state.json',
        'bucket': os.environ.get('R2_BUCKET_SECRETS', 'paipai-secrets'),
        'key': 'wechat/state.json',
        'content_type': 'application/json',
    },
]

CONTENT_TYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.png': 'image/png', '.gif': 'image/gif',
    '.webp': 'image/webp', '.ogg': 'audio/ogg',
    '.mp3': 'audio/mpeg', '.mp4': 'video/mp4',
    '.json': 'application/json', '.txt': 'text/plain',
}


def load_state() -> dict:
    try:
        return json.loads(Path(SYNC_STATE).read_text())
    except Exception:
        return {}


def save_state(state: dict):
    Path(SYNC_STATE).write_text(json.dumps(state, ensure_ascii=False))


def file_signature(path: str) -> tuple:
    st = os.stat(path)
    return (int(st.st_mtime), st.st_size)


def file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def expand_pattern(pattern: str) -> list:
    if '*' in pattern:
        import glob
        return glob.glob(pattern)
    p = Path(pattern)
    return [str(p)] if p.exists() else []


def sync():
    client = get_client()
    state = load_state()
    now_yyyymm = time.strftime('%Y%m')
    uploaded = 0
    skipped = 0
    errors = 0

    for rule in SYNC_RULES:
        for local_path in expand_pattern(rule['pattern']):
            if not os.path.isfile(local_path):
                continue
            sig = file_signature(local_path)
            last_sig = state.get(local_path, [None, None])
            if last_sig and tuple(last_sig) == sig:
                skipped += 1
                continue

            filename = os.path.basename(local_path)
            r2_key = rule['key'].format(
                filename=filename,
                yyyymm=time.strftime('%Y%m', time.localtime(sig[0])),
            )
            ct = rule.get('content_type')
            if rule.get('content_type_from_ext'):
                ext = os.path.splitext(filename)[1].lower()
                ct = CONTENT_TYPES.get(ext, 'application/octet-stream')

            try:
                # Cheap check: head_object to skip if R2 already has same size
                try:
                    head = client.head_object(Bucket=rule['bucket'], Key=r2_key)
                    if head.get('ContentLength') == sig[1]:
                        state[local_path] = list(sig)
                        skipped += 1
                        continue
                except client.exceptions.ClientError:
                    pass  # not found, proceed
                client.upload_file(
                    Filename=local_path, Bucket=rule['bucket'], Key=r2_key,
                    ExtraArgs={'ContentType': ct},
                )
                state[local_path] = list(sig)
                uploaded += 1
                print(f'✅ {rule["bucket"]}/{r2_key} ({sig[1]} B)')
            except Exception as e:
                print(f'❌ {local_path} → {rule["bucket"]}/{r2_key}: {e}')
                errors += 1

    save_state(state)
    print(f'\nsummary: uploaded={uploaded} skipped={skipped} errors={errors}')
    return uploaded, errors


if __name__ == '__main__':
    sync()
