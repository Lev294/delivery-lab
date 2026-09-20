import os
from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def database_options(host=None):
    # Separate fields avoid URL escaping problems with generated passwords.
    return {
        "host": host or os.getenv("DB_HOST", "127.0.0.1"),
        "port": os.getenv("DB_PORT", "5432"),
        "dbname": os.getenv("DB_NAME", "delivery"),
        "user": os.getenv("DB_USER", "app"),
        "password": os.environ["DB_PASSWORD"],
        "connect_timeout": 3,
        "options": "-c statement_timeout=3000 -c lock_timeout=2000",
        "row_factory": dict_row,
    }


def make_pool(host=None):
    return ConnectionPool(
        kwargs=database_options(host),
        min_size=1,
        max_size=5,
        timeout=3,
        max_waiting=20,
        open=True,
        check=ConnectionPool.check_connection,
    )


@asynccontextmanager
async def lifespan(app):
    # The old demonstration endpoints can still run without a database.
    pool = None
    if os.getenv("DB_PASSWORD"):
        pool = make_pool()
    app.state.pool = pool
    replica = make_pool(os.environ["REPLICA_HOST"]) if os.getenv("REPLICA_HOST") else None
    app.state.replica = replica
    from delivery.inventory_client import InventoryClient

    app.state.inventory = InventoryClient()
    try:
        yield
    finally:
        app.state.inventory.close()
        if replica is not None:
            replica.close()
        if pool is not None:
            pool.close()
