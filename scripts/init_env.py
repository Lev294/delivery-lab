"""Generate local-only credentials without printing them or overwriting a file."""

import os
import secrets
from pathlib import Path

path = Path(__file__).resolve().parents[1] / ".env"
try:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print(".env already exists; kept unchanged")
else:
    with os.fdopen(descriptor, "w") as stream:
        stream.write(
            "APP_PORT=8080\n"
            f"POSTGRES_PASSWORD={secrets.token_hex(24)}\n"
            f"APP_DB_PASSWORD={secrets.token_hex(24)}\n"
        )
    print("Created .env with generated local credentials (not tracked by Git)")
