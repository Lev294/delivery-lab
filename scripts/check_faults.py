"""Controlled local failures; restores stopped services and WAL replay in finally blocks."""

import argparse
import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8080"


def compose(*args, env=None, check=True):
    result = subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=90
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return result


def sql(service, statement, check=True):
    return compose(
        "exec",
        "-T",
        service,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "postgres",
        "-d",
        "delivery",
        "-Atc",
        statement,
        check=check,
    )


def http(path, body=None, key=None, method=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(
        BASE + path,
        headers=headers,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
    )
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read().decode()
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"raw": raw[:100]}
        return response.status, body


def wait_for(predicate, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (OSError, KeyError):
            pass
        time.sleep(0.5)
    raise AssertionError("Recovery/replication did not reach expected state")


def new_order(key=None):
    return http("/orders", {"sku": "demo-item", "quantity": 1}, key or str(uuid4()))


def inventory_ready():
    # HAProxy round-robin: check repeated calls, not just one recovered client's channel.
    return all(http("/inventory/demo-item")[0] == 200 for _ in range(4))


def replica_lag():
    status, order = new_order()
    assert status == 201
    path = f"/replica/orders/{order['id']}"
    wait_for(lambda: http(path)[0] == 200)
    assert sql("replica", "SELECT pg_is_in_recovery()").stdout.strip() == "t"
    readonly = sql("replica", "UPDATE orders SET status=status WHERE false", check=False)
    assert readonly.returncode != 0 and "read-only" in readonly.stderr
    try:
        sql("replica", "SELECT pg_wal_replay_pause()")
        wait_for(
            lambda: (
                sql("replica", "SELECT pg_get_wal_replay_pause_state()").stdout.strip() == "paused"
            )
        )
        assert (
            http(f"/orders/{order['id']}/status", {"status": "completed"}, method="PATCH")[0] == 200
        )
        assert http(path)[1]["status"] == "reserved"
    finally:
        sql("replica", "SELECT pg_wal_replay_resume()")
    wait_for(lambda: http(path)[1].get("status") == "completed")
    return "read-only confirmed; paused replica was stale; resumed replica caught up"


def dependency_timeout():
    key = str(uuid4())
    wait_for(inventory_ready)
    before = http("/inventory/demo-item")[1]["available"]
    try:
        compose(
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "inventory",
            env=dict(os.environ, INVENTORY_DELAY_SECONDS="3"),
        )
        wait_for(inventory_ready)
        assert new_order(key)[0] == 504
        state = sql(
            "postgres", f"SELECT status FROM orders WHERE idempotency_key='{key}'"
        ).stdout.strip()
        assert state == "pending"
        assert http("/inventory/demo-item")[1]["available"] == before - 1
    finally:
        compose(
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "inventory",
            env=dict(os.environ, INVENTORY_DELAY_SECONDS="0"),
        )
        wait_for(inventory_ready)
    wait_for(lambda: new_order(key)[0] == 200)
    assert http("/inventory/demo-item")[1]["available"] == before - 1
    return "504 after committed reservation; same-key retry finalized without double decrement"


def inventory_outage():
    key = str(uuid4())
    try:
        compose("stop", "inventory")
        assert new_order(key)[0] in (503, 504)
    finally:
        compose("start", "inventory")
        wait_for(inventory_ready)
    wait_for(lambda: new_order(key)[0] == 200)
    return "pending order recovered after Inventory restart"


def application_outage():
    try:
        compose("stop", "app")
        wait_for(
            lambda: all(http("/health/live")[1].get("instance_id") == "orders-2" for _ in range(8))
        )
    finally:
        compose("start", "app")
        wait_for(lambda: http("/health/live")[1].get("instance_id") == "orders-1")
    return "new requests served by surviving instance; both instances restored"


def database_outage():
    _, order = new_order()
    try:
        compose("stop", "postgres")
        wait_for(lambda: http("/health/ready")[0] == 503)
        assert new_order()[0] == 503
    finally:
        compose("start", "postgres")
        wait_for(lambda: http("/health/ready")[0] == 200)
    assert http(f"/orders/{order['id']}")[1] == order
    return "write service unavailable without primary; order persisted after restart"


def websocket_outage():
    _, order = new_order()
    process = subprocess.Popen(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "test",
            "python",
            "-m",
            "scripts.check_stack",
            "--ws-failover",
            order["id"],
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines = queue.Queue()

    def read_line():
        lines.put(process.stdout.readline())

    threading.Thread(target=read_line, daemon=True).start()
    service = None
    try:
        first = json.loads(lines.get(timeout=25))
        service = "app" if first["connected_to"] == "orders-1" else "app2"
        compose("stop", "--timeout", "2", service)
        output, error = process.communicate(timeout=40)
        assert process.returncode == 0, output + error
        assert json.loads(output.strip())["passed"] is True
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        if service:
            compose("start", service)
            expected = "orders-1" if service == "app" else "orders-2"
            wait_for(lambda: http("/health/live")[1].get("instance_id") == expected)
    return "socket closed with its server; client reconnected through proxy and got snapshot"


def main():
    global BASE
    checks = {
        "app": application_outage,
        "inventory": inventory_outage,
        "timeout": dependency_timeout,
        "replica": replica_lag,
        "websocket": websocket_outage,
        "database": database_outage,
    }
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=checks)
    parser.add_argument("--url", default=BASE)
    args = parser.parse_args()
    BASE = args.url
    for name, check in checks.items():
        if args.only is None or args.only == name:
            print(json.dumps({"check": name, "passed": True, "observation": check()}), flush=True)


if __name__ == "__main__":
    main()
