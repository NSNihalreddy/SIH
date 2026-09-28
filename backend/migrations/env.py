import sys
from pathlib import Path
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure the backend package is importable even when Alembic is launched from
# outside the backend working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import SYNC_DATABASE_URL
from app.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", SYNC_DATABASE_URL.replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # spatial_ref_sys is owned by PostGIS, not by the application schema.
    if type_ == "table" and reflected and name == "spatial_ref_sys":
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(url=SYNC_DATABASE_URL, target_metadata=target_metadata, include_object=include_object, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
