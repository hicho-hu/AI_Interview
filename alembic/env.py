from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import Settings
from app.db.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
url = config.attributes.get("database_url") or Settings().database_url
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
if url.startswith("sqlite:///"):
    from pathlib import Path

    Path(url[len("sqlite:///") :]).parent.mkdir(parents=True, exist_ok=True)


def run_migrations():
    if context.is_offline_mode():
        context.configure(
            url=url, target_metadata=Base.metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine = engine_from_config(
            config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool
        )
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                render_as_batch=url.startswith("sqlite"),
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
        engine.dispose()


run_migrations()
