from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatabaseConfig:
    backend: str
    sqlite_path: Path | None = None
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "kennybot"
    password: str = ""
    database: str = "kennybot"
    charset: str = "utf8mb4"


def resolve_database_config(path: Path | None = None, *, backend: str | None = None) -> DatabaseConfig:
    env_backend = os.environ.get("KENNYBOT_DB_BACKEND")
    if backend is not None:
        resolved_backend = backend.strip().lower()
    elif env_backend:
        resolved_backend = env_backend.strip().lower()
    elif path is not None:
        resolved_backend = "sqlite"
    else:
        resolved_backend = "mariadb"
    if resolved_backend == "sqlite":
        sqlite_path = path
        if sqlite_path is None:
            raise ValueError("sqlite backend requires a path")
        return DatabaseConfig(backend="sqlite", sqlite_path=sqlite_path)
    if resolved_backend != "mariadb":
        raise ValueError(f"unsupported database backend: {resolved_backend}")
    return DatabaseConfig(
        backend="mariadb",
        sqlite_path=path,
        host=os.environ.get("KENNYBOT_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("KENNYBOT_DB_PORT", "3306")),
        user=os.environ.get("KENNYBOT_DB_USER", "kennybot"),
        password=os.environ.get("KENNYBOT_DB_PASSWORD", ""),
        database=os.environ.get("KENNYBOT_DB_NAME", "kennybot"),
        charset=os.environ.get("KENNYBOT_DB_CHARSET", "utf8mb4"),
    )


def connect_database(config: DatabaseConfig) -> Any:
    if config.backend == "sqlite":
        assert config.sqlite_path is not None
        conn = sqlite3.connect(config.sqlite_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    import pymysql
    from pymysql.cursors import DictCursor

    admin = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        charset=config.charset,
        autocommit=True,
        cursorclass=DictCursor,
    )
    try:
        db_name = config.database.replace("`", "``")
        with admin.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                f"CHARACTER SET {config.charset} COLLATE {config.charset}_unicode_ci"
            )
    finally:
        admin.close()

    return pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset=config.charset,
        autocommit=False,
        cursorclass=DictCursor,
    )


def sql_placeholders(sql: str, config: DatabaseConfig) -> str:
    if config.backend == "sqlite":
        return sql
    return sql.replace("?", "%s")
