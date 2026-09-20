"""Generate protobuf/gRPC code, or verify committed generated files in CI."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
with tempfile.TemporaryDirectory() as directory:
    destination = directory if args.check else str(root)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            "-Iproto",
            f"--python_out={destination}",
            f"--grpc_python_out={destination}",
            "proto/delivery/rpc/inventory.proto",
        ],
        cwd=root,
        check=True,
    )
    if args.check:
        for filename in ("inventory_pb2.py", "inventory_pb2_grpc.py"):
            path = Path("delivery/rpc") / filename
            assert (root / path).read_bytes() == (Path(directory) / path).read_bytes(), filename
print("gRPC generated files match .proto" if args.check else "gRPC files regenerated")
