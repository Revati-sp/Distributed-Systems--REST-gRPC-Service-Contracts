"""Regenerate the Python gRPC code from inventory.proto.

Usage (from the project root, with the virtualenv active):
    python grpc_service/generate_code.py

This is equivalent to running:
    python -m grpc_tools.protoc -I grpc_service \
        --python_out=grpc_service/generated \
        --grpc_python_out=grpc_service/generated \
        grpc_service/inventory.proto
"""

import sys
from pathlib import Path

from grpc_tools import protoc

GRPC_DIR = Path(__file__).resolve().parent
GENERATED_DIR = GRPC_DIR / "generated"
PROTO_FILE = GRPC_DIR / "inventory.proto"


def main() -> int:
    GENERATED_DIR.mkdir(exist_ok=True)
    exit_code = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{GRPC_DIR}",
            f"--python_out={GENERATED_DIR}",  # message classes -> inventory_pb2.py
            f"--grpc_python_out={GENERATED_DIR}",  # stub/servicer -> inventory_pb2_grpc.py
            str(PROTO_FILE),
        ]
    )
    if exit_code == 0:
        print(f"Generated gRPC code in {GENERATED_DIR}")
    else:
        print(f"protoc failed with exit code {exit_code}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
