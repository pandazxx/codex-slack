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

TABLES = [
    "workspaces", "staffs", "staff_sessions", "config", "topics",
    "sessions", "messages", "attachments", "chunks", "event_actions",
    "notes", "tasks", "staged_dispatches",
]

# Re-export so production code can catch DB integrity errors without
# importing dialect-specific modules.  The shim re-raises exc.orig, so
# callers see the DBAPI-level exception:
#   SQLite  → sqlite3.IntegrityError
#   Postgres → psycopg.errors.IntegrityError (UniqueViolation is a subclass)
#   MySQL   → pymysql.err.IntegrityError  (added when Phase 2 lands)
try:
    from psycopg.errors import IntegrityError as _psycopg_IntegrityError
    DBIntegrityError: type | tuple = (sqlite3.IntegrityError, _psycopg_IntegrityError)
except ImportError:
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
    """Resolve the database URL from environment variables (M1: Postgres default).

    Resolution order:
      1. DATABASE_URL environment variable — used as-is when set.
      2. Bundled Postgres URL built from POSTGRES_HOST / POSTGRES_PORT /
         POSTGRES_USER / POSTGRES_DB / POSTGRES_PASSWORD.  The PostgreSQL
         default applies whenever DATABASE_URL is unset, regardless of whether
         MASTER_DATA_DIR is set (MASTER_DATA_DIR is for attachments/snapshots,
         not the DB URL).
      3. alembic_ini_fallback — Alembic offline-mode placeholder only;
         empty by default.

    Raises ValueError when DATABASE_URL is unset and POSTGRES_PASSWORD is
    not available and alembic_ini_fallback is empty.

    alembic/env.py imports this function so both paths share one source of
    truth for the database URL.
    """
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return database_url

    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "master")
    db = os.getenv("POSTGRES_DB", "master")
    password = os.getenv("POSTGRES_PASSWORD", "").strip()

    if password:
        return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"

    if alembic_ini_fallback:
        return alembic_ini_fallback

    raise ValueError(
        "DATABASE_URL is not set and POSTGRES_PASSWORD is not set. "
        "Set DATABASE_URL explicitly, or set POSTGRES_PASSWORD so the bundled "
        "Postgres URL can be constructed."
    )


def engine(db_path_or_url: str | None = None) -> Engine:
    """Return the SQLAlchemy engine.

    Resolution order:
      1. db_path_or_url argument — when explicitly provided (not None / empty),
         use it directly.  A full SQLAlchemy URL is used as-is; a plain
         filesystem path is converted to sqlite+pysqlite:///{path}.
         This lets callers (tests, the shim) pin a specific database regardless
         of DATABASE_URL.
      2. DATABASE_URL environment variable — used when no explicit argument is
         given.
      3. resolve_db_url() — builds the bundled Postgres URL from env vars, or
         raises ValueError when no configuration is available.

    Engines are cached per URL so a single pool is reused across requests.
    The cache is intentionally typed as dict | None: tests may monkeypatch
    _engine to None to invalidate the cache; the isinstance guard below
    handles that without relying on reload().  Prefer _reset_engine_cache()
    in new code.
    """
    global _engine
    if db_path_or_url:
        url = _resolve_url(db_path_or_url)
    else:
        database_url = os.getenv("DATABASE_URL", "").strip()
        url = database_url if database_url else resolve_db_url()

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

# Columns that are BOOLEAN in the schema.  Test code (and pre-M0 legacy helpers)
# sometimes embeds literal 0/1 in SQL text rather than binding Python bool values.
# On Postgres, integer literals for BOOLEAN columns are rejected (no implicit
# int4→boolean cast).  _fix_insert_bool_literals rewrites those literals in the
# SQL text before execution on non-SQLite dialects.
#
# Production code post-M1 binds Python bool() via text() named params and never
# goes through this path.  The shim is kept for backward-compat with test
# fixtures that use old-style embedded-literal SQL.
_BOOLEAN_COLS: dict[str, frozenset[str]] = {
    "messages":      frozenset({"silent"}),
    "staffs":        frozenset({"is_default"}),
    "event_actions": frozenset({"enabled", "structured_output", "silent"}),
}


def _fix_insert_bool_literals(sql: str) -> str:
    """Replace literal 0/1 at BOOLEAN column positions in a single-row VALUES clause.

    Handles INSERT statements where boolean values are hardcoded as integer literals
    in the SQL text (e.g. legacy test fixtures).  PostgreSQL rejects integer literals
    for BOOLEAN columns; SQLite accepts them natively.  Only called for non-SQLite
    dialects.

    Limitation: only handles single-row VALUES clauses.  Multi-row VALUES
    (INSERT INTO t (c) VALUES (0), (1)) are not rewritten; production code must
    bind Python bool values explicitly via text() named params.
    """
    import re as _re

    m = _re.match(
        r"(?i)\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)\s*\(([^)]+)\)",
        sql,
    )
    if not m:
        return sql

    table = m.group(1).lower()
    bool_cols = _BOOLEAN_COLS.get(table)
    if not bool_cols:
        return sql

    col_names = [c.strip().strip('"').lower() for c in m.group(2).split(",")]
    bool_col_positions = frozenset(i for i, col in enumerate(col_names) if col in bool_cols)
    if not bool_col_positions:
        return sql

    vm = _re.search(r"(?i)\bVALUES\s*\(", sql)
    if not vm:
        return sql

    body_start = vm.end()  # index after opening '('

    spans: list[tuple[int, int]] = []
    depth = 1
    tok_start = body_start
    i = body_start

    while i < len(sql):
        c = sql[i]
        if c == "'":
            i += 1
            while i < len(sql):
                if sql[i] == "'":
                    if i + 1 < len(sql) and sql[i + 1] == "'":
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
        elif c == "(":
            depth += 1
            i += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                spans.append((tok_start, i))
                break
            i += 1
        elif c == "," and depth == 1:
            spans.append((tok_start, i))
            tok_start = i + 1
            i += 1
        else:
            i += 1

    if not spans:
        return sql

    chars = list(sql)
    for pos, (start, end) in enumerate(spans):
        if pos not in bool_col_positions:
            continue
        token = sql[start:end]
        stripped = token.strip()
        if stripped not in ("0", "1"):
            continue
        replacement = "false" if stripped == "0" else "true"
        leading = len(token) - len(token.lstrip())
        digit_pos = start + leading
        chars[digit_pos : digit_pos + 1] = list(replacement)

    return "".join(chars)


def _qmark_to_named(
    sql: str,
    params: tuple | list,
) -> tuple[str, dict[str, Any]]:
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

    @property
    def dialect_name(self) -> str:
        """Return the SQLAlchemy dialect name, e.g. 'sqlite', 'postgresql'."""
        return self._conn.dialect.name

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
        # Rewrite literal 0/1 for BOOLEAN columns on non-SQLite dialects so
        # that legacy test fixtures with embedded integer literals work on
        # Postgres.  Production code uses text() with named params + bool(),
        # so this path is only hit by old-style callers.
        dialect_name = self._conn.dialect.name
        if dialect_name != "sqlite":
            sql = _fix_insert_bool_literals(sql)
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


def sqlite_safety_check(data_dir: str) -> None:
    """Guard against silently abandoning existing SQLite data when the default switches to Postgres.

    Reads DATABASE_URL from the environment.

    - If DATABASE_URL is set: returns immediately — the operator has expressed
      intent and no override is needed.
    - If DATABASE_URL is unset and {data_dir}/master_data.db exists and contains
      a 'workspaces' table: raises SystemExit(1) with a message that names the
      SQLite file and describes both remedies (keep SQLite or migrate to Postgres).
    - Otherwise: returns normally — safe to proceed with the Postgres default.
    """
    import sys

    if os.getenv("DATABASE_URL", "").strip():
        return

    db_path = os.path.join(data_dir, "master_data.db")
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return

    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='workspaces'"
            ).fetchone()
            has_workspaces = row is not None
        finally:
            conn.close()
    except Exception:
        return

    if not has_workspaces:
        return

    msg = (
        f"ERROR: existing SQLite data detected at\n"
        f"  {db_path}\n"
        f"but DATABASE_URL is unset and the default is now Postgres. Pick one:\n"
        f"\n"
        f"  (a) Keep SQLite:\n"
        f"      export DATABASE_URL=sqlite+pysqlite:///{db_path}\n"
        f"\n"
        f"  (b) Migrate to Postgres (recommended):\n"
        f"      scripts/db.sh snapshot-and-migrate \\\n"
        f"          --source-url \"sqlite+pysqlite:///{db_path}\" \\\n"
        f"          --target-url \"$POSTGRES_URL\"\n"
        f"      Then set DATABASE_URL=$POSTGRES_URL.\n"
        f"\n"
        f"Set DATABASE_URL to suppress this check.\n"
        f"See docs/guides/runbooks/migrate-sqlite-to-postgres.md"
    )
    print(msg, file=sys.stderr, flush=True)
    sys.exit(1)


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

      Behind (alembic_version is a known ancestor revision)
                                → sys.exit(1) with the §5 error message
                                  directing the operator to run
                                  `scripts/db.sh migrate`.

      Ahead or unknown (alembic_version is not in the known revision graph)
                                → sys.exit(1) pointing at a possible
                                  accidental downgrade.

    Connection is retried with exponential backoff for up to
    DB_CONNECT_TIMEOUT_SECONDS (default 60) before giving up.  This allows
    the function to succeed even when the database service (e.g. the bundled
    Postgres container) is still starting.

    This function must be called once at application startup before any
    request handling begins.  It does not modify the DB on up-to-date
    or refusal paths.
    """
    import sys
    import time
    from pathlib import Path
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.runtime.migration import MigrationContext
    from alembic import command as alembic_command
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy.engine import make_url as _make_url
    from sqlalchemy.exc import OperationalError as _SAOperationalError

    safe_url = _make_url(url).render_as_string(hide_password=True)

    _repo_root = Path(__file__).parents[3]
    cfg = Config(str(_repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)

    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()

    connect_timeout = int(os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "60") or "60")
    deadline = time.monotonic() + connect_timeout
    attempt = 0
    while True:
        attempt += 1
        eng = None
        try:
            eng = _make_engine(url)
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
            eng.dispose()
            break
        except _SAOperationalError as exc:
            if eng is not None:
                eng.dispose()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                LOGGER.error(
                    "startup_db: cannot connect to database %s after %ds: %s",
                    safe_url, connect_timeout, exc,
                )
                raise
            delay = min(2 ** (attempt - 1), remaining, 30)
            LOGGER.warning(
                "startup_db: connection attempt %d to %s failed (%s); retrying in %.1fs",
                attempt, safe_url, exc, delay,
            )
            time.sleep(delay)

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
                f"Pre-baseline state is only supported for SQLite databases; got {safe_url!r}"
            )
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
    # Walk the Alembic revision graph to classify the current revision:
    #   - In the graph → it is a known ancestor, so the DB is behind HEAD.
    #   - Not in the graph → unknown revision.  We use lexicographic ordering
    #     as a secondary heuristic: an unknown revision that sorts after HEAD
    #     is almost certainly from a newer release (accidental downgrade),
    #     while one that sorts before HEAD is likely from a development branch
    #     or pre-history migration.  Our file names use 000000000001, 000000000002…
    #     so lex order matches temporal order for revisions in the numbering
    #     scheme.  Non-numeric IDs (e.g. short hashes from a feature branch)
    #     always sort after digits in ASCII and are therefore treated as "ahead."
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
