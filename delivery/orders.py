"""Business operations and explicit SQL; no HTTP or network clients here yet."""

from uuid import uuid4


class OrderError(Exception):
    def __init__(self, code: str, status_code: int):
        self.code = code
        self.status_code = status_code


ORDER_COLUMNS = "id, sku, quantity, status, created_at, updated_at"


def create_order(pool, key, sku, quantity):
    # Pool context commits on normal exit and rolls back on an exception.
    # Both the order and stock change belong to this same transaction.
    with pool.connection() as conn:
        order = conn.execute(
            f"""INSERT INTO orders (id, idempotency_key, sku, quantity, status)
                VALUES (%s, %s, %s, %s, 'reserved')
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING {ORDER_COLUMNS}""",
            (uuid4(), key, sku, quantity),
        ).fetchone()
        if order is None:
            existing = conn.execute(
                f"SELECT {ORDER_COLUMNS} FROM orders WHERE idempotency_key = %s",
                (key,),
            ).fetchone()
            if existing["sku"] != sku or existing["quantity"] != quantity:
                raise OrderError("idempotency_key_conflict", 409)
            return existing, False

        stock = conn.execute(
            """UPDATE inventory SET available = available - %s
               WHERE sku = %s AND available >= %s RETURNING available""",
            (quantity, sku, quantity),
        ).fetchone()
        if stock is None:
            exists = conn.execute("SELECT 1 FROM inventory WHERE sku = %s", (sku,)).fetchone()
            raise OrderError("out_of_stock" if exists else "unknown_sku", 409 if exists else 404)
        return order, True


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
                WHERE id = %s RETURNING {ORDER_COLUMNS}""",
            (order_id,),
        ).fetchone()
        if order is None:
            raise OrderError("order_not_found", 404)
        return order


def get_inventory(pool, sku):
    with pool.connection() as conn:
        stock = conn.execute(
            "SELECT sku, available FROM inventory WHERE sku = %s", (sku,)
        ).fetchone()
        if stock is None:
            raise OrderError("unknown_sku", 404)
        return stock
