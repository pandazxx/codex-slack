"""Database access layer — SQLAlchemy Core + legacy SQLite shim.

Public API (M0):
  engine(db_path)        → SQLAlchemy Engine
  get_connection(db_path) → _ConnectionShim (behaves like sqlite3.Connection)
  init_db(db_path)       → run the legacy SQLite schema/migration sequence
  schema_info(db_path)   → dict of table → column names
  TABLES                 → ordered list of all table names
  DBIntegrityError       → exception type to catch on constraint violations

The _ConnectionShim accepts both old-style qmark ? / tuple parameters
(for backward-compatible call sites and tests) and SQLAlchemy text() / dict
parameters (for new production code).  It re-raises the original DBAPI
exception from SQLAlchemy's wrapped IntegrityError so that code catching
the underlying exception (e.g. sqlite3.IntegrityError in tests) continues
to work in M0.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from typing import Any

from sqlalchemy import create_engine, event, text as sa_text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError as _SAIntegrityError
from sqlalchemy.sql.elements import TextClause

from ._prebaseline import init_db_sqlite
from ._row import _Row

LOGGER = logging.getLogger(__name__)

TABLES = [
    "workspaces", "staffs", "staff_sessions", "config", "topics",
    "sessions", "messages", "attachments", "chunks", "event_actions",
    "notes", "tasks", "staged_dispatches",
]

# Re-export so production code can catch DB integrity errors without
# importing sqlite3 directly.  In M0 this is sqlite3.IntegrityError
# because the shim re-raises e.orig; in M1 it becomes
# sqlalchemy.exc.IntegrityError when the Postgres path is live.
DBIntegrityError = sqlite3.IntegrityError

# ──────────────────────────────────────────────────────────────────────────────
# Engine management
# ──────────────────────────────────────────────────────────────────────────────

# Per-URL engine cache. Named _engine (not _engines) so test fixtures that
# patch it back to None can invalidate the cache via monkeypatch.setattr.
_engine: dict[str, Engine] | None = {}
_engine_lock = threading.Lock()


def _make_engine(url: str) -> Engine:
    """Build a SQLAlchemy engine for the given URL with sensible defaults.

    SQLite pool selection:
      - :memory: databases use StaticPool so the single in-memory DB is shared
        across all checkouts (closing the connection would destroy it).
      - File-based SQLite databases use NullPool so each checkout opens a fresh
        OS-level connection, matching the old sqlite3.connect() behaviour.
        This prevents a single DBAPI connection from being shared across the
        FastAPI threadpool, the MQTT callback thread, and background threads
        without a mutex.
    """
    pool_size = int(os.getenv("DB_POOL_SIZE", "5") or "5")
    pool_max_overflow = int(os.getenv("DB_POOL_MAX_OVERFLOW", "10") or "10")
    echo = os.getenv("DB_ECHO_SQL", "false").lower() in ("1", "true", "yes")

    kwargs: dict[str, Any] = {"echo": echo}

    if url.startswith("sqlite"):
        is_memory = ":memory:" in url
        if is_memory:
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
        else:
            from sqlalchemy.pool import NullPool
            kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = pool_size
        kwargs["max_overflow"] = pool_max_overflow

    eng = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _set_sqlite_pragmas(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

    return eng


def _db_url_from_path(db_path: str) -> str:
    return f"sqlite+pysqlite:///{db_path}"


def _resolve_url(db_path_or_url: str | None) -> str:
    """Resolve a database path or URL to a canonical SQLAlchemy URL string.

    Accepts:
      - A full SQLAlchemy URL (contains '://') — used as-is.
      - A filesystem path — converted to sqlite+pysqlite:///path.
    """
    if db_path_or_url and "://" in db_path_or_url:
        return db_path_or_url
    if db_path_or_url:
        return _db_url_from_path(db_path_or_url)
    return ""


def resolve_db_url(*, alembic_ini_fallback: str = "") -> str:
    """Resolve the database URL from environment variables.

    Resolution order:
      1. DATABASE_URL environment variable.
      2. MASTER_DATA_DIR environment variable →
         sqlite+pysqlite:///{MASTER_DATA_DIR}/master_data.db
      3. alembic_ini_fallback — alembic offline-mode placeholder only;
         empty by default.

    Raises ValueError when no source is available and alembic_ini_fallback
    is empty.  alembic/env.py imports this function so both paths use one
    source of truth for the default filename (master_data.db).
    """
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return database_url
    data_dir = os.getenv("MASTER_DATA_DIR", "").strip()
    if data_dir:
        return f"sqlite+pysqlite:///{data_dir}/master_data.db"
    if alembic_ini_fallback:
        return alembic_ini_fallback
    raise ValueError("Provide DATABASE_URL or MASTER_DATA_DIR")


def engine(db_path_or_url: str | None = None) -> Engine:
    """Return the SQLAlchemy engine.

    Resolution order:
      1. DATABASE_URL environment variable (explicit operator setting).
      2. db_path_or_url argument — full URL used as-is; plain path converted
         to sqlite+pysqlite:///{path}.
      3. resolve_db_url() → MASTER_DATA_DIR/master_data.db, or raises.

    Engines are cached per URL so a single pool is reused across requests.
    The cache is intentionally typed as dict | None: tests may monkeypatch
    _engine to None to invalidate the cache; the isinstance guard below
    handles that without relying on reload().  Prefer _reset_engine_cache()
    in new code.
    """
    global _engine
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        url = database_url
    else:
        url = _resolve_url(db_path_or_url)
        if not url:
            url = resolve_db_url()  # raises ValueError when MASTER_DATA_DIR unset

    with _engine_lock:
        if not isinstance(_engine, dict):
            _engine = {}
        if url not in _engine:
            _engine[url] = _make_engine(url)
        return _engine[url]


def _reset_engine_cache() -> None:
    """Clear the per-URL engine cache.

    Use this in tests that need a fresh engine, rather than monkeypatching
    _engine directly.  Both approaches work: engine() re-initialises _engine
    to {} when it finds None, so monkeypatch.setattr(db_mod, "_engine", None)
    also invalidates the cache for existing test fixtures.
    """
    global _engine
    with _engine_lock:
        _engine = {}


# ──────────────────────────────────────────────────────────────────────────────
# Result / row wrappers
# ──────────────────────────────────────────────────────────────────────────────

class _ResultProxy:
    """Wraps a SQLAlchemy CursorResult to return _Row objects."""

    __slots__ = ("_result",)

    def __init__(self, result) -> None:
        self._result = result

    def fetchone(self) -> _Row | None:
        row = self._result.fetchone()
        return _Row(row) if row is not None else None

    def fetchall(self) -> list[_Row]:
        return [_Row(r) for r in self._result.fetchall()]

    def __iter__(self):
        return (_Row(r) for r in self._result)


# ──────────────────────────────────────────────────────────────────────────────
# Param-style conversion
# ──────────────────────────────────────────────────────────────────────────────

def _qmark_to_named(sql: str, params: tuple | list) -> tuple[str, dict[str, Any]]:
    """Convert qmark ? placeholders to :_p0, :_p1, … named params for SQLAlchemy.

    Walks the SQL character by character so that ? inside single-quoted string
    literals is left untouched.  Standard SQL string literal rules apply:
      - A single-quoted literal begins with ' and ends with the next '
      - Two consecutive '' inside a literal is an escaped single quote
    """
    param_dict: dict[str, Any] = {}
    result: list[str] = []
    idx = 0
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            # Consume the entire single-quoted literal without substitution.
            result.append(ch)
            i += 1
            while i < len(sql):
                c = sql[i]
                result.append(c)
                i += 1
                if c == "'":
                    # A second consecutive ' is an escaped quote; continue literal.
                    if i < len(sql) and sql[i] == "'":
                        result.append(sql[i])
                        i += 1
                    else:
                        break
        elif ch == "?":
            name = f"_p{idx}"
            param_dict[name] = params[idx]
            idx += 1
            result.append(f":{name}")
            i += 1
        else:
            result.append(ch)
            i += 1
    return "".join(result), param_dict


# ──────────────────────────────────────────────────────────────────────────────
# Connection shim
# ──────────────────────────────────────────────────────────────────────────────

class _ConnectionShim:
    """Thin wrapper around a SQLAlchemy Connection.

    Accepts both old-style qmark ? + tuple/list params (for backward-compatible
    test code and call sites not yet converted) and new-style text() + dict
    params (for converted production code).

    On SQLite, IntegrityError is re-raised as the original sqlite3 exception
    so that tests doing pytest.raises(sqlite3.IntegrityError) continue to pass.
    """

    __slots__ = ("_conn", "_closed")

    def __init__(self, sa_conn) -> None:
        self._conn = sa_conn
        self._closed = False

    def execute(self, sql, params=None) -> _ResultProxy:
        if isinstance(sql, TextClause):
            # New style: sqlalchemy text() with dict params.
            sa_params = params if params is not None else {}
            try:
                result = self._conn.execute(sql, sa_params)
            except _SAIntegrityError as exc:
                if exc.orig is not None:
                    raise exc.orig from exc
                raise
            return _ResultProxy(result)

        # Old style: plain string, params may be tuple/list or None.
        if params is not None and isinstance(params, (tuple, list)):
            converted_sql, named_params = _qmark_to_named(sql, params)
            sql_obj = sa_text(converted_sql)
        else:
            sql_obj = sa_text(sql)
            named_params = params if isinstance(params, dict) else {}

        try:
            result = self._conn.execute(sql_obj, named_params)
        except _SAIntegrityError as exc:
            if exc.orig is not None:
                raise exc.orig from exc
            raise
        return _ResultProxy(result)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        """Close the underlying SQLAlchemy connection.

        Idempotent: safe to call more than once.  Under NullPool this returns
        the OS-level connection to the OS immediately; under StaticPool (:memory:)
        it releases the single shared connection back to the pool.
        """
        if not self._closed:
            self._closed = True
            self._conn.close()

    def __enter__(self) -> "_ConnectionShim":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self.close()
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def get_connection(db_path_or_url: str) -> _ConnectionShim:
    """Open and return a connection to the database as a _ConnectionShim.

    Accepts either a filesystem path (legacy callers) or a full SQLAlchemy
    URL string (e.g. 'sqlite+pysqlite:///...' as used by tests and the
    Alembic env).

    The returned object behaves like sqlite3.Connection for the method
    subset used across production modules: execute(), commit(), close(),
    and the context-manager protocol (with conn: … commits on exit).

    Rows returned by execute() support both named column access (row["col"])
    and positional access (row[0]), and are dict()-convertible via keys().
    """
    sa_conn = engine(db_path_or_url).connect()
    return _ConnectionShim(sa_conn)


def init_db(db_path: str) -> None:
    """Initialise the database.

    M0: delegates to the legacy SQLite path in _prebaseline.  In M1 this
    will be replaced by Alembic's upgrade-on-empty-DB logic.
    """
    init_db_sqlite(db_path)


def schema_info(db_path: str) -> dict[str, list[str]]:
    """Return a mapping of table → [column_name, …] for all known tables."""
    conn = get_connection(db_path)
    try:
        result: dict[str, list[str]] = {}
        for table in TABLES:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            result[table] = [row["name"] for row in rows]
        return result
    finally:
        conn.close()
