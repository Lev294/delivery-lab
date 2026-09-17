import os
from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def database_options():
    # Separate fields avoid URL escaping problems with generated passwords.
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": os.getenv("DB_PORT", "5432"),
        "dbname": os.getenv("DB_NAME", "delivery"),
        "user": os.getenv("DB_USER", "app"),
        "password": os.environ["DB_PASSWORD"],
        "connect_timeout": 3,
        "options": "-c statement_timeout=3000 -c lock_timeout=2000",
        "row_factory": dict_row,
    }


@asynccontextmanager
async def lifespan(app):
    # The old demonstration endpoints can still run without a database.
    pool = None
    if os.getenv("DB_PASSWORD"):
        pool = ConnectionPool(
            kwargs=database_options(),
            min_size=1,
            max_size=5,
            timeout=3,
            max_waiting=20,
            open=True,
            check=ConnectionPool.check_connection,
        )
    app.state.pool = pool
    try:
        yield
    finally:
        if pool is not None:
            pool.close()
