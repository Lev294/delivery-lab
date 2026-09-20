"""Orders orchestration. SQL transactions never span the Inventory network call."""

from uuid import uuid4


class OrderError(Exception):
    def __init__(self, code: str, status_code: int):
        self.code = code
        self.status_code = status_code


ORDER_COLUMNS = "id, sku, quantity, status, failure_reason, created_at, updated_at"


def create_order(pool, inventory, key, sku, quantity):
    # Transaction 1 durably records intent, then releases the connection before gRPC.
    with pool.connection() as conn:
        order = conn.execute(
            f"""INSERT INTO orders (id, idempotency_key, sku, quantity, status)
                VALUES (%s, %s, %s, %s, 'pending')
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING {ORDER_COLUMNS}""",
            (uuid4(), key, sku, quantity),
        ).fetchone()
        created = order is not None
        if not created:
            existing = conn.execute(
                f"SELECT {ORDER_COLUMNS} FROM orders WHERE idempotency_key = %s",
                (key,),
            ).fetchone()
            if existing["sku"] != sku or existing["quantity"] != quantity:
                raise OrderError("idempotency_key_conflict", 409)
            order = existing
    if order["status"] == "pending":
        outcome = inventory.reserve(order["id"], sku, quantity)
        # Transaction 2 finalizes the order. Concurrent retries cannot undo completion.
        with pool.connection() as conn:
            conn.execute(
                """UPDATE orders SET status=%s, failure_reason=%s, updated_at=now()
                   WHERE id=%s AND status='pending'""",
                (
                    "reserved" if outcome == "reserved" else "rejected",
                    None if outcome == "reserved" else outcome,
                    order["id"],
                ),
            )
        order = get_order(pool, order["id"])
    if order["status"] == "rejected":
        reason = order["failure_reason"]
        raise OrderError(reason, 404 if reason == "unknown_sku" else 409)
    return order, created


def get_order(pool, order_id):
    with pool.connection() as conn:
        order = conn.execute(
            f"SELECT {ORDER_COLUMNS} FROM orders WHERE id = %s", (order_id,)
        ).fetchone()
        if order is None:
            raise OrderError("order_not_found", 404)
        return order


def complete_order(pool, order_id):
    with pool.connection() as conn:
        order = conn.execute(
            f"""UPDATE orders
                SET updated_at = CASE WHEN status <> 'completed' THEN now() ELSE updated_at END,
                    status = 'completed'
                WHERE id = %s AND status IN ('reserved', 'completed') RETURNING {ORDER_COLUMNS}""",
            (order_id,),
        ).fetchone()
        if order is None:
            if conn.execute("SELECT 1 FROM orders WHERE id=%s", (order_id,)).fetchone():
                raise OrderError("order_not_reserved", 409)
            raise OrderError("order_not_found", 404)
        return order
