import logging
import os
from urllib.parse import quote_plus

import psycopg2
from psycopg2.extensions import connection as pg_connection
from sqlalchemy import create_engine, engine

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="[%(asctime)s] - %(levelname)s - %(process)d: %(message)s",
    datefmt="%d.%m.%Y %H:%M:%S",
)
logger.setLevel("DEBUG")


def get_pg_connection() -> pg_connection:
    """Get pg_connection"""
    connection = psycopg2.connect(
        user=quote_plus(os.getenv("ALEAN_POSTGRES_USER", "alean_user")),
        password=quote_plus(os.getenv("ALEAN_POSTGRES_PASSWORD", "alean_password")),
        dsn=psycopg2.extensions.make_dsn(
            host=os.getenv("ALEAN_POSTGRES_HOST", "localhost"),
            port=os.getenv("ALEAN_POSTGRES_PORT", "5433"),
            dbname=os.getenv("ALEAN_POSTGRES_DB", "alean_db"),
        ),
    )
    connection.autocommit = True
    return connection


def get_pg_engine() -> engine.Engine:
    """Get sqlalchemy.engine.Engine"""
    url_template = "postgresql://{username}:{password}@{host}:{port}/{database}"
    pg_engine = create_engine(
        url_template.format(
            username=os.getenv("ALEAN_POSTGRES_USER", "postgres_user"),
            password=os.getenv("ALEAN_POSTGRES_PASSWORD", "postgres_password"),
            host=os.getenv("ALEAN_POSTGRES_HOST", "postgres_host"),
            port=os.getenv("ALEAN_POSTGRES_PORT", "5432"),
            database=os.getenv("ALEAN_POSTGRES_DB", "postgres_db"),
        )
    )
    return pg_engine
