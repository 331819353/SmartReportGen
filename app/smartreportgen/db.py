"""PostgreSQL 连接池与初始化。"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import settings

log = logging.getLogger(__name__)
_pool: ConnectionPool | None = None

EMBED_TABLES = ("dataset.dataset", "style_rule.style_rule")


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row, "autocommit": True},
            open=True,
        )
    return _pool


@contextmanager
def conn() -> Iterator[psycopg.Connection]:
    with pool().connection() as c:
        yield c


def fetchall(sql: str, params: Any = None) -> list[dict]:
    with conn() as c:
        return c.execute(sql, params).fetchall()


def fetchone(sql: str, params: Any = None) -> dict | None:
    with conn() as c:
        return c.execute(sql, params).fetchone()


def execute(sql: str, params: Any = None) -> None:
    with conn() as c:
        c.execute(sql, params)


def jsonb(v: Any) -> Jsonb:
    return Jsonb(v)


def get_setting(key: str) -> str | None:
    row = fetchone("SELECT value FROM sys.setting WHERE key=%s", (key,))
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO sys.setting(key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
        (key, value),
    )


def wait_for_db(timeout: float = 90) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with psycopg.connect(settings.database_url, connect_timeout=5) as c:
                c.execute("SELECT 1")
            return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2)
    raise RuntimeError(f"数据库不可用: {last}")


def _vector_dim(c: psycopg.Connection, table: str) -> int | None:
    row = c.execute(
        "SELECT atttypmod FROM pg_attribute WHERE attrelid = %s::regclass AND attname = 'embedding'",
        (table,),
    ).fetchone()
    return row["atttypmod"] if row else None


def init_db() -> bool:
    """建库建表；若向量维度或向量模型变化则调整列并返回 True（需要重建向量）。"""
    wait_for_db()
    dim = settings.embedding_dim
    ddl = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8").replace("{dim}", str(dim))
    need_reembed = False
    with conn() as c:
        c.execute("SELECT pg_advisory_lock(4242001)")
        try:
            c.execute(ddl)
            for table in EMBED_TABLES:
                cur = _vector_dim(c, table)
                if cur is not None and cur != dim:
                    log.warning("%s.embedding 维度 %s -> %s，重建列", table, cur, dim)
                    c.execute(f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector({dim}) USING NULL")
                    need_reembed = True
            if dim <= 2000:
                c.execute(
                    "CREATE INDEX IF NOT EXISTS dataset_embedding_hnsw ON dataset.dataset "
                    "USING hnsw (embedding vector_cosine_ops)"
                )
            row = c.execute("SELECT value FROM sys.setting WHERE key='embedding_signature'").fetchone()
            if not row or row["value"] != settings.embedding_signature:
                need_reembed = True
        finally:
            c.execute("SELECT pg_advisory_unlock(4242001)")
    return need_reembed
