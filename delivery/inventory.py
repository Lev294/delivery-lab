"""Inventory service: the only owner of inventory/reservations SQL."""

import json
import logging
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import grpc
import psycopg
from psycopg_pool import PoolTimeout

from delivery.db import make_pool
from delivery.rpc import inventory_pb2 as pb
from delivery.rpc import inventory_pb2_grpc as rpc

logger = logging.getLogger(__name__)


class Inventory(rpc.InventoryServicer):
    def __init__(self, pool, delay_after_commit=0):
        self.pool = pool
        self.delay_after_commit = delay_after_commit

    def Reserve(self, request, context):
        try:
            order_id = UUID(request.order_id)
        except ValueError:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid order ID")
        if not 1 <= request.quantity <= 10000 or not request.sku:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid quantity or sku")
        try:
            with self.pool.connection() as conn:
                inserted = conn.execute(
                    """INSERT INTO reservations (order_id, sku, quantity, outcome)
                       VALUES (%s, %s, %s, 'reserved') ON CONFLICT DO NOTHING RETURNING order_id""",
                    (order_id, request.sku, request.quantity),
                ).fetchone()
                if not inserted:
                    row = conn.execute(
                        "SELECT * FROM reservations WHERE order_id=%s", (order_id,)
                    ).fetchone()
                    if row["sku"] != request.sku or row["quantity"] != request.quantity:
                        context.abort(grpc.StatusCode.FAILED_PRECONDITION, "reservation conflict")
                    outcome = row["outcome"]
                else:
                    stock = conn.execute(
                        """UPDATE inventory SET available=available-%s
                           WHERE sku=%s AND available >= %s RETURNING available""",
                        (request.quantity, request.sku, request.quantity),
                    ).fetchone()
                    outcome = "reserved"
                    if stock is None:
                        exists = conn.execute(
                            "SELECT 1 FROM inventory WHERE sku=%s", (request.sku,)
                        ).fetchone()
                        outcome = "out_of_stock" if exists else "unknown_sku"
                        conn.execute(
                            "UPDATE reservations SET outcome=%s WHERE order_id=%s",
                            (outcome, order_id),
                        )
            # A controlled lab fault: commit succeeds but the reply misses the client's deadline.
            logger.info(
                json.dumps(
                    {
                        "event": "reserve",
                        "order_id": str(order_id),
                        "outcome": outcome,
                        "replayed": not bool(inserted),
                    }
                )
            )
            if self.delay_after_commit:
                time.sleep(self.delay_after_commit)
            return pb.ReserveReply(outcome=outcome)
        except (psycopg.Error, PoolTimeout):
            context.abort(grpc.StatusCode.UNAVAILABLE, "inventory database unavailable")

    def Stock(self, request, context):
        try:
            with self.pool.connection() as conn:
                row = conn.execute(
                    "SELECT * FROM inventory WHERE sku=%s", (request.sku,)
                ).fetchone()
            if row is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "unknown sku")
            return pb.StockReply(**row)
        except (psycopg.Error, PoolTimeout):
            context.abort(grpc.StatusCode.UNAVAILABLE, "inventory database unavailable")


def start_server(pool, address="[::]:50051", delay=0):
    server = grpc.server(ThreadPoolExecutor(max_workers=8), maximum_concurrent_rpcs=32)
    service = Inventory(pool, delay)
    rpc.add_InventoryServicer_to_server(service, server)
    port = server.add_insecure_port(address)
    server.start()
    return server, port, service


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with make_pool() as pool:
        server, _, _ = start_server(pool, delay=float(os.getenv("INVENTORY_DELAY_SECONDS", "0")))
        stopped = threading.Event()

        def stop(*_):
            server.stop(3)
            stopped.set()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        stopped.wait()


if __name__ == "__main__":
    main()
