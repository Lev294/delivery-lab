import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from delivery.db import database_options
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
        conn.execute("TRUNCATE orders, inventory")
        conn.execute("INSERT INTO inventory (sku, available) VALUES ('demo-item', 100)")


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client
