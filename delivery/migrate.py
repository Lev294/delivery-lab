"""Apply ordered SQL migrations once, with a ledger and an advisory lock."""

from pathlib import Path

import psycopg

from delivery.db import database_options


def migrate():
    with psycopg.connect(**database_options()) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(7340201)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY)")
        for path in sorted((Path(__file__).parent.parent / "migrations").glob("*.sql")):
            applied = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE name = %s", (path.name,)
            ).fetchone()
            if applied:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            print(f"applied {path.name}", flush=True)


if __name__ == "__main__":
    migrate()
