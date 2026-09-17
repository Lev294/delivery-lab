CREATE TABLE inventory (
    sku text PRIMARY KEY,
    available integer NOT NULL CHECK (available >= 0)
);

CREATE TABLE orders (
    id uuid PRIMARY KEY,
    idempotency_key uuid NOT NULL UNIQUE,
    sku text NOT NULL,
    quantity integer NOT NULL CHECK (quantity > 0),
    status text NOT NULL CHECK (status IN ('reserved', 'completed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO inventory (sku, available) VALUES ('demo-item', 100);
