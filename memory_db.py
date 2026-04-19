#!/usr/bin/env python3
"""
派派记忆系统 — SQLite schema + 连接管理

L2 主记忆索引存在 /root/paipai/memory.db
L3 原始数据在 R2 paipai-archive/raw/

schema 设计：
  modules: 闭环模块（一次会话 / 一个功能改造）
  events:  每次用户交互 / 消息（细粒度）
  tags:    多对多标签表
  module_tags: 关联

v1 不做向量（sqlite-vec 未装），用关键词 + 时间权重。
"""
import sqlite3
import os
from pathlib import Path

DB_PATH = '/root/paipai/memory.db'


def get_conn(path: str = DB_PATH) -> sqlite3.Connection:
    """返回带外键的连接（默认 DELETE 日志模式，避免 WAL+trigger 兼容问题）。"""
    conn = sqlite3.connect(path, timeout=10)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA = """
-- 闭环模块 (一次完整的主题对话 / 一个代码改造)
CREATE TABLE IF NOT EXISTS modules (
    id              TEXT PRIMARY KEY,          -- uuid 或 session_id
    source          TEXT NOT NULL,             -- 'claude_session' / 'paipai_inbox' / 'digest'
    start_ts        REAL NOT NULL,             -- 开始时间戳
    end_ts          REAL,                      -- 结束时间戳
    category        TEXT,                      -- 'investment' / 'infra' / 'chitchat' / ...
    title           TEXT,                      -- 人类可读标题
    summary         TEXT,                      -- 一段摘要
    tags            TEXT,                      -- 逗号分隔标签
    entities        TEXT,                      -- JSON array: 涉及的股票/人/项目
    token_spent     INTEGER DEFAULT 0,         -- 估算 token 用量
    r2_pointer      TEXT,                      -- r2://paipai-archive/raw/modules/<id>/
    status          TEXT DEFAULT 'raw',        -- raw / distilled / folded
    folded_into     TEXT,                      -- 如 '2026-W16' 若已被周折叠
    created_at      REAL DEFAULT (strftime('%s','now')),
    updated_at      REAL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_modules_start_ts ON modules(start_ts DESC);
CREATE INDEX IF NOT EXISTS idx_modules_category ON modules(category);
CREATE INDEX IF NOT EXISTS idx_modules_status   ON modules(status);

-- 原始事件（可选细粒度表，消息级）
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id       TEXT REFERENCES modules(id),
    ts              REAL NOT NULL,
    role            TEXT NOT NULL,             -- user / assistant / tool
    content         TEXT,                      -- 摘要或原文前 N 字
    tokens          INTEGER,
    created_at      REAL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_events_module ON events(module_id);
CREATE INDEX IF NOT EXISTS idx_events_ts     ON events(ts DESC);

-- 折叠索引（日/周/月）
CREATE TABLE IF NOT EXISTS foldings (
    id              TEXT PRIMARY KEY,          -- 'daily:2026-04-19' / 'weekly:2026-W16'
    level           TEXT NOT NULL,             -- daily / weekly / monthly
    period          TEXT NOT NULL,             -- 2026-04-19 / 2026-W16 / 2026-04
    module_count    INTEGER,
    summary         TEXT,                      -- 当期综合摘要
    key_events      TEXT,                      -- JSON array of module_id
    token_spent     INTEGER,
    created_at      REAL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_foldings_level_period ON foldings(level, period);

-- 全文搜索虚拟表（FTS5）— 独立表，不用 content= shadow，避免 trigger 复杂性
CREATE VIRTUAL TABLE IF NOT EXISTS modules_fts USING fts5(
    id UNINDEXED, title, summary, tags, entities,
    tokenize='unicode61'
);
"""


def reindex_fts(conn=None):
    """Rebuild FTS from modules table."""
    close_after = False
    if conn is None:
        conn = get_conn(); close_after = True
    conn.execute('DELETE FROM modules_fts')
    conn.execute("""
        INSERT INTO modules_fts(id, title, summary, tags, entities)
        SELECT id, COALESCE(title,''), COALESCE(summary,''),
               COALESCE(tags,''), COALESCE(entities,'')
        FROM modules
    """)
    conn.commit()
    if close_after:
        conn.close()


def init(path: str = DB_PATH):
    first = not os.path.exists(path)
    conn = get_conn(path)
    conn.executescript(SCHEMA)
    conn.close()
    if first:
        print(f'✅ initialized new DB at {path}')
    else:
        print(f'✅ schema migration OK at {path}')


if __name__ == '__main__':
    init()
    conn = get_conn()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print('tables:', [r[0] for r in cur.fetchall()])
    conn.close()
