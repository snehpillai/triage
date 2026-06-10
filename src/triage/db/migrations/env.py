import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `triage` importable when running alembic from the project root
sys.path.insert(0, str(Path(__file__).parents[3]))

from triage.config import settings  # noqa: E402
from triage.db.models import Base  # noqa: E402

# Alembic Config object - gives access to alembic.ini values
alembic_config = context.config

# Wire in our database URL so it never lives in alembic.ini
alembic_config.set_main_option("sqlalchemy.url", settings.database_url)

# Set up Python logging from alembic.ini
if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

# Our models' metadata - alembic reads this for autogenerate support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL script without a live database connection."""
    url = alembic_config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
