import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from delivery.db import database_options, make_pool
from delivery.inventory import start_server
from delivery.migrate import migrate
from main import app


@pytest.fixture(scope="session", autouse=True)
def schema():
    # Never truncate the user's demonstration database.
    if os.getenv("DB_NAME") != "delivery_test":
        pytest.fail("Tests require isolated DB_NAME=delivery_test; use the Compose test service")
    migrate()


@pytest.fixture(autouse=True)
def reset_data(schema):
    with psycopg.connect(**database_options()) as conn:
        conn.execute("TRUNCATE orders, inventory, reservations")
        conn.execute("INSERT INTO inventory (sku, available) VALUES ('demo-item', 100)")


@pytest.fixture
def inventory_service(monkeypatch):
    with make_pool() as pool:
        server, port, service = start_server(pool, "127.0.0.1:0")
        monkeypatch.setenv("INVENTORY_TARGET", f"127.0.0.1:{port}")
        yield service
        server.stop(0).wait(5)


@pytest.fixture
def client(inventory_service):
    with TestClient(app) as client:
        yield client
