"""Alembic environment — delegates URL resolution to src/master/db/__init__.py."""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the src package importable when running alembic from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Import after sys.path is set so the src package is visible.
from src.master.db import resolve_db_url  # noqa: E402

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False prevents alembic's fileConfig call from
    # disabling loggers that were created before alembic runs (e.g. during
    # application startup).  The Python default of True would silence every
    # logger not listed in alembic.ini for the rest of the process lifetime,
    # breaking caplog-based tests and production log capture.
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def run_migrations_offline() -> None:
    """Run migrations in offline mode (generate SQL script, no live connection).

    Offline mode does not require a live database — it only generates SQL.
    Falls back to the alembic.ini placeholder URL so the caller can pass an
    explicit URL via the --url flag without needing environment variables.
    """
    url = resolve_db_url(
        alembic_ini_fallback=config.get_main_option("sqlalchemy.url", "")
    )
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection.

    Requires DATABASE_URL or POSTGRES_PASSWORD to be set; raises ValueError
    with a clear message if neither is available.  The alembic.ini placeholder
    URL is NOT used here — it would silently create a stray master.db in the
    repository root when the caller forgets to set environment variables.
    """
    url = resolve_db_url()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = url
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
