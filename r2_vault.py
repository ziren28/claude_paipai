#!/usr/bin/env python3
"""
派派 R2 保险库客户端 — S3 API 封装

用法:
  from r2_vault import get_client, get_secret, put_secret, list_bucket, upload_file, download_file
"""
import os
from pathlib import Path

import boto3
from botocore.client import Config


def _load_env():
    """Load /root/paipai/.env into os.environ (idempotent)."""
    env_path = Path('/root/paipai/.env')
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k, v.strip('"').strip("'"))


_client = None


def get_client():
    """Cached S3 client bound to Cloudflare R2."""
    global _client
    if _client is not None:
        return _client
    _load_env()
    _client = boto3.client(
        's3',
        endpoint_url=os.environ['R2_ENDPOINT'],
        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
        region_name='auto',
        config=Config(signature_version='s3v4'),
    )
    return _client


def get_object_bytes(bucket: str, key: str) -> bytes:
    return get_client().get_object(Bucket=bucket, Key=key)['Body'].read()


def put_object_bytes(bucket: str, key: str, data: bytes,
                     content_type: str = 'application/octet-stream'):
    return get_client().put_object(
        Bucket=bucket, Key=key, Body=data, ContentType=content_type,
    )


def upload_file(bucket: str, key: str, local_path: str,
                content_type: str = 'application/octet-stream'):
    return get_client().upload_file(
        Filename=local_path, Bucket=bucket, Key=key,
        ExtraArgs={'ContentType': content_type},
    )


def download_file(bucket: str, key: str, local_path: str):
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    return get_client().download_file(
        Bucket=bucket, Key=key, Filename=local_path,
    )


def list_bucket(bucket: str, prefix: str = '') -> list:
    out = []
    paginator = get_client().get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents', []):
            out.append({'key': obj['Key'], 'size': obj['Size'],
                        'modified': obj['LastModified'].isoformat()})
    return out


def secret_get(key: str) -> str:
    """Read secret from paipai-secrets bucket."""
    bucket = os.environ.get('R2_BUCKET_SECRETS', 'paipai-secrets')
    return get_object_bytes(bucket, key).decode('utf-8')


def secret_put(key: str, value: str):
    bucket = os.environ.get('R2_BUCKET_SECRETS', 'paipai-secrets')
    return put_object_bytes(bucket, key, value.encode('utf-8'),
                             content_type='text/plain')


if __name__ == '__main__':
    import sys, json
    if len(sys.argv) < 2:
        print(__doc__)
        print("commands: list <bucket> [prefix] | get <bucket> <key> | put <bucket> <key> <file>")
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == 'list':
        bucket = sys.argv[2]
        prefix = sys.argv[3] if len(sys.argv) > 3 else ''
        for o in list_bucket(bucket, prefix):
            print(f'{o["size"]:>10}  {o["modified"][:19]}  {o["key"]}')
    elif cmd == 'get':
        data = get_object_bytes(sys.argv[2], sys.argv[3])
        sys.stdout.buffer.write(data)
    elif cmd == 'put':
        with open(sys.argv[4], 'rb') as f:
            put_object_bytes(sys.argv[2], sys.argv[3], f.read())
        print(f'uploaded {sys.argv[4]} → {sys.argv[2]}/{sys.argv[3]}')
    else:
        print(f'unknown command: {cmd}')
