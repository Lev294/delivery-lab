"""Small real HTTP demonstration; uses only the Python standard library."""

import argparse
import json
from urllib.request import Request, urlopen
from uuid import uuid4


def request(base, path, body=None, method="GET", key=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    data = None if body is None else json.dumps(body).encode()
    with urlopen(
        Request(base + path, data=data, method=method, headers=headers), timeout=10
    ) as response:
        payload = json.load(response)
        print(response.status, method, path, json.dumps(payload, ensure_ascii=False))
        return response.status, payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    key = str(uuid4())
    status, order = request(args.url, "/orders", {"sku": "demo-item", "quantity": 1}, "POST", key)
    assert status == 201 and order["status"] == "reserved"
    status, repeated = request(
        args.url, "/orders", {"sku": "demo-item", "quantity": 1}, "POST", key
    )
    assert status == 200 and repeated == order
    _, completed = request(
        args.url, f"/orders/{order['id']}/status", {"status": "completed"}, "PATCH"
    )
    assert completed["status"] == "completed"
    _, fetched = request(args.url, f"/orders/{order['id']}")
    assert fetched == completed
    request(args.url, "/inventory/demo-item")
    print("PASS: create, repeat, complete, read (consumes one item)")


if __name__ == "__main__":
    main()
