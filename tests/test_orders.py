from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from delivery.db import database_options


def create(client, quantity=2, key=None, sku="demo-item"):
    return client.post(
        "/orders",
        json={"sku": sku, "quantity": quantity},
        headers={"Idempotency-Key": str(key or uuid4())},
    )


def test_order_lifecycle(client):
    response = create(client)
    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "reserved"
    assert client.get("/inventory/demo-item").json()["available"] == 98
    assert client.get(response.headers["location"]).json() == order
    completed = client.patch(response.headers["location"] + "/status", json={"status": "completed"})
    assert completed.status_code == 200 and completed.json()["status"] == "completed"
    again = client.patch(response.headers["location"] + "/status", json={"status": "completed"})
    assert again.json() == completed.json()
    assert client.get("/inventory/demo-item").json()["available"] == 98
    assert response.headers["x-request-id"] and response.headers["x-instance-id"]


def test_repeated_creation(client):
    key = uuid4()
    first = create(client, key=key)
    repeated = create(client, key=key)
    assert first.status_code == 201 and repeated.status_code == 200
    assert first.json() == repeated.json()
    assert client.get("/inventory/demo-item").json()["available"] == 98
    conflict = create(client, key=key, quantity=3)
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "idempotency_key_conflict"}


def test_insufficient_stock_rolls_back_and_key_can_be_retried(client):
    key = uuid4()
    response = create(client, quantity=101, key=key)
    assert response.status_code == 409
    with psycopg.connect(**database_options()) as conn:
        assert conn.execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 0
        assert conn.execute("SELECT available FROM inventory").fetchone()["available"] == 100
        conn.execute("UPDATE inventory SET available = 101")
    assert create(client, quantity=101, key=key).status_code == 201


@pytest.mark.parametrize("quantity", [0, -1, 10001, 1.5, "2", True])
def test_quantity_validation(client, quantity):
    assert create(client, quantity=quantity).status_code == 422
    assert client.get("/inventory/demo-item").json()["available"] == 100


def test_missing_key_unknown_sku_and_order(client):
    assert client.post("/orders", json={"sku": "demo-item", "quantity": 1}).status_code == 422
    assert create(client, sku="missing").status_code == 404
    assert client.get(f"/orders/{uuid4()}").status_code == 404
    assert (
        client.patch(f"/orders/{uuid4()}/status", json={"status": "completed"}).status_code == 404
    )
    with psycopg.connect(**database_options()) as conn:
        assert conn.execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 0


def test_invalid_transition(client):
    order = create(client).json()
    assert (
        client.patch(f"/orders/{order['id']}/status", json={"status": "cancelled"}).status_code
        == 422
    )
    assert client.get(f"/orders/{order['id']}").json()["status"] == "reserved"


def test_concurrent_duplicate_requests(client):
    key = uuid4()
    with ThreadPoolExecutor(max_workers=8) as workers:
        responses = list(workers.map(lambda _: create(client, key=key), range(8)))
    assert sorted(response.status_code for response in responses) == [200] * 7 + [201]
    assert len({response.json()["id"] for response in responses}) == 1
    assert client.get("/inventory/demo-item").json()["available"] == 98


def test_concurrent_reservations_do_not_oversell(client):
    with psycopg.connect(**database_options()) as conn:
        conn.execute("UPDATE inventory SET available = 5")
    with ThreadPoolExecutor(max_workers=8) as workers:
        responses = list(workers.map(lambda _: create(client, quantity=1), range(8)))
    assert sorted(response.status_code for response in responses) == [201] * 5 + [409] * 3
    assert client.get("/inventory/demo-item").json()["available"] == 0
    with psycopg.connect(**database_options()) as conn:
        assert conn.execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 5


def test_database_constraint_rejects_negative_stock(client):
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(**database_options()) as conn:
            conn.execute("UPDATE inventory SET available = -1")
    assert client.get("/inventory/demo-item").json()["available"] == 100


def test_legacy_routes_and_health(client):
    assert client.get("/hello/Lev").json() == {"message": "Hello, Lev!"}
    assert client.get("/delivery/estimate/5").json() == {"distance_km": 5, "estimated_minutes": 25}
    assert client.get("/delivery/estimate/0").status_code == 200
    assert client.get("/delivery/estimate/50").status_code == 200
    for value in (-1, 51, "abc"):
        assert client.get(f"/delivery/estimate/{value}").status_code == 422
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
