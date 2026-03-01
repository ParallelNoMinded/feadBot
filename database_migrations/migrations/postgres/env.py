# flake8: noqa
import os
from logging.config import fileConfig
from urllib.parse import quote_plus

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# Ensure models are imported so SQLModel.metadata is populated for autogenerate
from shared_models import tables as _shared_tables  # noqa: F401


def generate_sqlalchemy_url() -> str:
    """
    Generate sqlalchemy url for postgresql using env variables
    """

    username = quote_plus(os.getenv("POSTGRES_USER", "alean_user"))
    password = quote_plus(os.getenv("POSTGRES_PASSWORD", "alean_password"))
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5433")
    database = quote_plus(os.getenv("POSTGRES_DB", "alean_db"))
    url_template = f"postgresql://{username}:{password}@{host}:{port}/{database}"
    return url_template


def set_sqlalchemy_url_safely(config, url: str) -> None:
    """
    Set sqlalchemy.url in a way that avoids ConfigParser interpolation issues
    """
    # Escape % characters to prevent ConfigParser interpolation issues
    escaped_url = url.replace("%", "%%")
    config.set_main_option("sqlalchemy.url", escaped_url)


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
set_sqlalchemy_url_safely(config, generate_sqlalchemy_url())

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = [SQLModel.metadata]

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_schemas=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    config_section = config.get_section(config.config_ini_section)

    if not config_section:
        raise ValueError("config section is None")

    connectable = engine_from_config(
        config_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
