"""Alembic environment — uses the same URL resolution as src/master/db/__init__.py."""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the src package importable when running alembic from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_db_url() -> str:
    """Mirror the resolution order in src/master/db/__init__.py engine()."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return database_url
    data_dir = os.getenv("MASTER_DATA_DIR", "").strip()
    if data_dir:
        return f"sqlite+pysqlite:///{data_dir}/master.db"
    # Fall back to the placeholder URL from alembic.ini so offline mode works.
    return config.get_main_option("sqlalchemy.url", "")


def run_migrations_offline() -> None:
    """Run migrations in offline mode (generate SQL script, no live connection)."""
    url = _resolve_db_url()
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
    url = _resolve_db_url()
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
