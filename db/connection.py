"""PostgreSQL 连接池（psycopg2）。"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2 import pool as pg_pool

logger = logging.getLogger(__name__)

_pool: pg_pool.ThreadedConnectionPool | None = None


def _database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL 未设置。请在 worker/.env 中配置，例如：\n"
            "DATABASE_URL=postgresql://wx:wx@localhost:5432/wxspirder"
        )
    return url


def get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        url = _database_url()
        _pool = pg_pool.ThreadedConnectionPool(1, 10, dsn=url, connect_timeout=5)
        logger.debug("PostgreSQL 连接池已创建: %s", url.split("@")[-1])
    return _pool


def _register_pgvector(conn: psycopg2.extensions.connection) -> None:
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
    except ImportError:
        logger.debug("pgvector 未安装，向量列将以字符串形式读写")


@contextmanager
def get_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    """从连接池取连接，自动提交或回滚，用完归还。"""
    p = get_pool()
    conn = p.getconn()
    _register_pgvector(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


def database_available() -> bool:
    """DATABASE_URL 是否已配置（不抛异常）。"""
    return bool(os.getenv("DATABASE_URL", "").strip())


def close_pool() -> None:
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        _pool = None
