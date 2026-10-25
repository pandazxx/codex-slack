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
    fileConfig(config.config_file_name)


def _resolve_alembic_url() -> str:
    """Return the database URL using the same resolution as src/master/db.engine().

    Falls back to the alembic.ini placeholder URL so offline mode (SQL script
    generation without a live connection) works without environment variables.
    """
    return resolve_db_url(
        alembic_ini_fallback=config.get_main_option("sqlalchemy.url", "")
    )


def run_migrations_offline() -> None:
    """Run migrations in offline mode (generate SQL script, no live connection)."""
    url = _resolve_alembic_url()
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    url = _resolve_alembic_url()
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
