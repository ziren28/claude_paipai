#!/usr/bin/env python3
"""
派派灾备 bootstrap — 从 R2 拉取所有凭证 + 状态文件。

前置：
  1. /root/paipai/.env 里已配置 R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY /
     R2_ENDPOINT / CLOUDFLARE_ACCOUNT_ID
  2. 网络可达 Cloudflare R2

流程：
  1. 连 r2://paipai-secrets/manifest.json 拿恢复清单
  2. 对每个文件 download_file → 设置权限
  3. 如 merge_into_env = true，把 .env.secrets 合并进 .env
"""
import json
import os
import sys
from pathlib import Path

from r2_vault import get_client, get_object_bytes, download_file


def main():
    print('=== 派派 bootstrap ===')
    bucket = os.environ.get('R2_BUCKET_SECRETS', 'paipai-secrets')

    # Pull manifest
    try:
        raw = get_object_bytes(bucket, 'manifest.json')
        manifest = json.loads(raw)
    except Exception as e:
        print(f'❌ failed to read manifest: {e}')
        sys.exit(1)
    print(f'manifest v{manifest.get("version")} · {len(manifest["files"])} files')

    env_extra = []
    for entry in manifest['files']:
        r2_key = entry['r2_key']
        local = entry['local_path']
        mode = entry.get('mode', 0o600)
        merge = entry.get('merge_into_env', False)

        try:
            download_file(bucket, r2_key, local)
            os.chmod(local, mode)
            print(f'✅ {r2_key} → {local} (mode {oct(mode)})')
        except Exception as e:
            print(f'❌ {r2_key}: {e}')
            continue

        if merge:
            env_extra.append(Path(local).read_text().strip())

    # Merge secrets into main .env
    if env_extra:
        env_path = Path('/root/paipai/.env')
        current = env_path.read_text() if env_path.exists() else ''
        existing_keys = set()
        for line in current.splitlines():
            if '=' in line and not line.startswith('#'):
                existing_keys.add(line.split('=', 1)[0])
        additions = []
        for block in env_extra:
            for line in block.splitlines():
                if '=' in line and not line.startswith('#'):
                    k = line.split('=', 1)[0]
                    if k not in existing_keys:
                        additions.append(line)
                        existing_keys.add(k)
        if additions:
            env_path.write_text(current.rstrip() + '\n# --- merged from R2 bootstrap ---\n' +
                                '\n'.join(additions) + '\n')
            os.chmod(env_path, 0o600)
            print(f'✅ merged {len(additions)} vars into .env')

    print('\n=== done. next step: systemctl start inbox-poller paipai-digest paipai-dailyhot ===')


if __name__ == '__main__':
    main()
