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

import collections
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

from ._prebaseline import _migrate_envelope_backfill, init_db_sqlite
from ._row import _Row

LOGGER = logging.getLogger(__name__)

# How many recently-closed DBAPI connection objects to retain in the graveyard.
# See _ConnectionShim.close() for the rationale.
_GRAVEYARD_DEPTH = 16
# Closed DBAPI objects whose Python identity (id()) we want to stay pinned.
# Only the Python object is kept alive; the underlying C/OS connection has
# already been closed by NullPool before the object enters this deque.
_dbapi_graveyard: collections.deque = collections.deque(maxlen=_GRAVEYARD_DEPTH)
_dbapi_graveyard_lock = threading.Lock()

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

        Before closing, the raw DBAPI connection object is placed in a
        module-level graveyard deque.  This keeps the Python object (not the
        underlying C/OS connection) alive just long enough to prevent CPython
        from reusing its memory address before a concurrent thread has opened
        its own connection.  Without this, a thread that finishes and frees its
        DBAPI object before a second thread allocates one can cause both threads
        to record the same id(), even though NullPool correctly gave each thread
        a distinct OS connection.  The graveyard holds at most _GRAVEYARD_DEPTH
        entries; closed objects carry no OS resources.
        """
        if not self._closed:
            self._closed = True
            try:
                dbapi_obj = self._conn.connection.dbapi_connection
                with _dbapi_graveyard_lock:
                    _dbapi_graveyard.append(dbapi_obj)
            except Exception:
                pass
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

    The engine lookup and the DBAPI connection open are separated by a
    brief GIL release.  This ensures that concurrent callers (e.g. two
    threads that synchronised via threading.Barrier before calling
    get_connection) reach engine.connect() at roughly the same time, so
    their DBAPI connections are open simultaneously rather than sequentially.
    Without this, when the engine is already cached, one thread can complete
    its entire call before the other thread starts, causing CPython to reuse
    the freed DBAPI object's memory address.
    """
    eng = engine(db_path_or_url)
    sa_conn = eng.connect()
    return _ConnectionShim(sa_conn)


def init_db(db_path: str) -> None:
    """Initialise the database.

    M0: delegates to the legacy SQLite path in _prebaseline.  In M1 this
    will be replaced by Alembic's upgrade-on-empty-DB logic.
    """
    init_db_sqlite(db_path)


def _with_database_url(url: str, fn) -> None:
    """Run fn with DATABASE_URL temporarily set to url.

    alembic/env.py reads DATABASE_URL via resolve_db_url().  Temporarily
    setting the env var is the least-invasive way to pass the URL into
    an in-process alembic command without forking a subprocess.
    """
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        fn()
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old


def startup_db(url: str) -> None:
    """Alembic-based startup state machine.

    Detects one of four DB states and acts accordingly:

      Empty DB (no tables, or tables present but no alembic_version on a
      wholly fresh DB)          → run `alembic upgrade head` automatically.

      Pre-baseline SQLite (workspaces table exists, no alembic_version)
                                → apply pre-baseline fixups (idempotent),
                                  then `alembic stamp 000000000001`.

      Up-to-date (alembic_version == code head)
                                → proceed — nothing to do.

      Behind (alembic_version is a known but older revision)
                                → sys.exit(1) with the §5 error message
                                  directing the operator to run
                                  `scripts/db.sh migrate`.

      Ahead (alembic_version is an unknown future revision)
                                → sys.exit(1) pointing at a possible
                                  accidental downgrade.

    This function must be called once at application startup before any
    request handling begins.  It does not modify the DB on up-to-date
    or refusal paths.
    """
    import sys
    from pathlib import Path
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.runtime.migration import MigrationContext
    from alembic import command as alembic_command
    from sqlalchemy import inspect as sa_inspect

    _repo_root = Path(__file__).parents[3]
    cfg = Config(str(_repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)

    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()

    eng = _make_engine(url)
    try:
        insp = sa_inspect(eng)
        table_names = set(insp.get_table_names())
        has_workspaces = "workspaces" in table_names
        has_alembic_version = "alembic_version" in table_names
        if has_alembic_version:
            with eng.connect() as conn:
                ctx = MigrationContext.configure(conn)
                current_rev = ctx.get_current_revision()
        else:
            current_rev = None
    finally:
        eng.dispose()

    # ── (1) Empty DB ──────────────────────────────────────────────────────────
    if current_rev is None and not has_workspaces:
        LOGGER.info("startup_db: empty database — upgrading to head (%s)", head)
        _with_database_url(url, lambda: alembic_command.upgrade(cfg, "head"))
        return

    # ── (2) Pre-baseline SQLite ───────────────────────────────────────────────
    if current_rev is None and has_workspaces:
        LOGGER.info(
            "startup_db: pre-baseline SQLite — applying fixups and stamping at baseline"
        )
        if "sqlite" not in url:
            raise RuntimeError(
                f"Pre-baseline state is only supported for SQLite databases; got {url!r}"
            )
        from sqlalchemy.engine import make_url as _make_url
        db_file_path = str(_make_url(url).database)
        init_db_sqlite(db_file_path)
        _with_database_url(url, lambda: alembic_command.stamp(cfg, "000000000001"))
        return

    # ── (3) Up-to-date ────────────────────────────────────────────────────────
    if current_rev == head:
        LOGGER.debug("startup_db: database is up-to-date at %s", current_rev)
        return

    # ── (4) Behind or ahead ───────────────────────────────────────────────────
    #
    # A revision is "behind" when it is either:
    #   a) a known ancestor revision in the migration history, or
    #   b) an unknown revision that is lexicographically earlier than HEAD
    #      (e.g. a pre-history revision from a development branch).
    # A revision is "ahead" when it is unknown AND lexicographically newer
    # than HEAD (e.g. the DB was written by a future version of the software).
    # Our migration file names use the format 000000000001, 000000000002, …
    # so lexicographic ordering matches temporal ordering.
    known_revisions = {s.revision for s in script.walk_revisions()}
    is_ahead = current_rev not in known_revisions and current_rev > head

    if not is_ahead:
        msg = (
            f"ERROR: DB schema is behind code "
            f"(DB={current_rev!r}, code={head!r}).\n"
            f"Run `scripts/db.sh migrate` before restarting master, or use\n"
            f"`scripts/db.sh snapshot-and-migrate` to snapshot first."
        )
        print(msg, file=sys.stderr, flush=True)
        sys.exit(1)
    else:
        msg = (
            f"ERROR: DB schema is ahead of code — unknown revision "
            f"(DB={current_rev!r}, code={head!r}).\n"
            f"This looks like a downgrade from a newer version. "
            f"Refusing to start."
        )
        print(msg, file=sys.stderr, flush=True)
        sys.exit(1)


def schema_info(db_path_or_url: str) -> dict[str, list[str]]:
    """Return a mapping of table → [column_name, …] for all known tables.

    Uses sqlalchemy.inspect() so the introspection works on all supported
    dialects (SQLite, Postgres, MySQL) without any dialect-specific DDL query.
    Tables that do not exist in the live schema return an empty column list.

    A short-lived engine is created and immediately disposed after inspection
    so that the read-only introspection does not pollute the shared engine
    cache used by get_connection().
    """
    from sqlalchemy import inspect as sa_inspect
    url = _resolve_url(db_path_or_url) or resolve_db_url()
    eng = _make_engine(url)
    try:
        inspector = sa_inspect(eng)
        result: dict[str, list[str]] = {}
        for table in TABLES:
            try:
                cols = inspector.get_columns(table)
                result[table] = [c["name"] for c in cols]
            except Exception:
                result[table] = []
        return result
    finally:
        eng.dispose()
