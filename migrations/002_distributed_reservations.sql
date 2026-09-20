ALTER TABLE orders DROP CONSTRAINT orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check
  CHECK (status IN ('pending', 'reserved', 'completed', 'rejected'));
ALTER TABLE orders ADD COLUMN failure_reason text;

CREATE TABLE reservations (
  order_id uuid PRIMARY KEY,
  sku text NOT NULL,
  quantity integer NOT NULL CHECK (quantity > 0),
  outcome text NOT NULL CHECK (outcome IN ('reserved', 'out_of_stock', 'unknown_sku'))
);
-- Preserve successful reservations from the single-service version, without a second decrement.
INSERT INTO reservations (order_id, sku, quantity, outcome)
SELECT id, sku, quantity, 'reserved' FROM orders WHERE status IN ('reserved', 'completed');
