"""Real network checks. Run in the Compose test container, not with ASGI TestClient."""

import argparse
import http.client
import json
import time
from uuid import uuid4

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

from scripts.demo import request


def distribution(port, fresh_connections=False):
    instances = []
    conn = None
    try:
        for _ in range(8):
            if conn is None:
                conn = http.client.HTTPConnection("proxy", port, timeout=5)
            conn.request("GET", "/health/live")
            response = conn.getresponse()
            assert response.status == 200
            instances.append(json.loads(response.read())["instance_id"])
            if fresh_connections:
                conn.close()
                conn = None
    finally:
        if conn:
            conn.close()
    return instances


def ws_failover(order_id):
    url = f"ws://proxy:8080/ws/orders/{order_id}"
    with connect(url, open_timeout=5) as ws:
        first = json.loads(ws.recv(timeout=5))
        print(json.dumps({"connected_to": first["instance_id"]}), flush=True)
        try:
            while True:
                ws.recv(timeout=30)
        except ConnectionClosed:
            pass
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with connect(url, open_timeout=2) as ws:
                snapshot = json.loads(ws.recv(timeout=5))
                assert snapshot["order"]["id"] == order_id
                assert snapshot["instance_id"] != first["instance_id"]
                print(
                    json.dumps({"reconnected_to": snapshot["instance_id"], "passed": True}),
                    flush=True,
                )
                return
        except (OSError, TimeoutError, AssertionError, WebSocketException):
            time.sleep(0.5)
    raise AssertionError("WebSocket did not reconnect to the surviving instance")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ws-failover")
    args = parser.parse_args()
    if args.ws_failover:
        ws_failover(args.ws_failover)
        return
    l7 = distribution(8080)
    l4 = distribution(8081)
    l4_new = distribution(8081, True)
    assert len(set(l7)) == 2, l7
    assert len(set(l4)) == 1, l4
    assert len(set(l4_new)) == 2, l4_new
    base = "http://proxy:8080"
    _, order = request(base, "/orders", {"sku": "demo-item", "quantity": 1}, "POST", str(uuid4()))
    with connect(f"ws://proxy:8080/ws/orders/{order['id']}", open_timeout=5) as ws:
        snapshot = json.loads(ws.recv(timeout=5))
        assert snapshot["order"]["status"] == "reserved"
        # Update through the OTHER Orders process: no process-local list of subscribers is needed.
        other = "app2" if snapshot["instance_id"] == "orders-1" else "app"
        request(
            f"http://{other}:8000",
            f"/orders/{order['id']}/status",
            {"status": "completed"},
            "PATCH",
        )
        assert json.loads(ws.recv(timeout=5))["order"]["status"] == "completed"
    _, info = request(base, "/replica/info")
    assert info["in_recovery"] is True
    for _ in range(30):
        try:
            _, replicated = request(base, f"/replica/orders/{order['id']}")
            if replicated["status"] == "completed":
                break
        except OSError:
            pass
        time.sleep(0.2)
    else:
        raise AssertionError("Replication did not catch up")
    print(
        json.dumps(
            {
                "l7_same_tcp": l7,
                "l4_same_tcp": l4,
                "l4_new_tcp": l4_new,
                "websocket_cross_instance": "passed",
                "physical_replica": "passed",
            }
        )
    )


if __name__ == "__main__":
    main()
