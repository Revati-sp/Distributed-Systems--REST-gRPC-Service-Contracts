"""gRPC Inventory Service.

Implements the InventoryService defined in inventory.proto.

Run:
    python grpc_service/inventory_server.py               # normal mode, port 50051
    python grpc_service/inventory_server.py --delay 5     # slow mode (5s per CheckInventory)
    INVENTORY_DELAY_SECONDS=5 python grpc_service/inventory_server.py

The delay applies only to CheckInventory; HealthCheck always answers instantly.
"""

import argparse
import logging
import os
import re
import signal
import sys
import time
from concurrent import futures
from pathlib import Path

import grpc

# The generated inventory_pb2_grpc.py does `import inventory_pb2`, so the
# generated/ folder must be importable as a top-level location.
sys.path.insert(0, str(Path(__file__).resolve().parent / "generated"))
import inventory_pb2  # noqa: E402
import inventory_pb2_grpc  # noqa: E402

DEFAULT_PORT = int(os.environ.get("INVENTORY_GRPC_PORT", "50051"))
DEFAULT_DELAY_SECONDS = float(os.environ.get("INVENTORY_DELAY_SECONDS", "0"))

INVENTORY = {
    "item-1": 10,
    "item-2": 5,
    "item-3": 0,
}

ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_QUANTITY = 1_000_000

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [inventory-grpc] %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)


class InventoryServicer(inventory_pb2_grpc.InventoryServiceServicer):
    """Server-side implementation of the generated InventoryServiceServicer base class."""

    def __init__(self, delay_seconds):
        self.delay_seconds = delay_seconds
        self.start_time = time.time()

    def CheckInventory(self, request, context):
        if self.delay_seconds > 0:
            logger.info("Delay mode: sleeping %.1fs before answering", self.delay_seconds)
            time.sleep(self.delay_seconds)
            # The client's deadline travels with the call. If it has already
            # passed, the client has given up (DEADLINE_EXCEEDED) and the
            # RPC is no longer active - there is no point doing more work.
            if not context.is_active():
                logger.warning(
                    "Client deadline already expired for item_id=%r; abandoning request",
                    request.item_id,
                )
                return inventory_pb2.CheckInventoryResponse()

        item_id = request.item_id
        quantity = request.quantity

        # In gRPC, errors are signalled with a status code + details string
        # (context.abort raises and ends the RPC), not with a JSON error body.
        if not item_id.strip():
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "item_id is required.")
        if not ITEM_ID_PATTERN.match(item_id):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "item_id may only contain letters, digits, '-' or '_' (max 64 chars).",
            )
        # proto3 int32 has no "missing" state: an omitted quantity arrives as 0.
        if quantity <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "quantity must be greater than 0.")
        if quantity > MAX_QUANTITY:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, f"quantity must not exceed {MAX_QUANTITY}."
            )

        if item_id not in INVENTORY:
            context.abort(grpc.StatusCode.NOT_FOUND, f"Item '{item_id}' does not exist.")

        in_stock = INVENTORY[item_id]
        if quantity > in_stock:
            context.abort(
                grpc.StatusCode.RESOURCE_EXHAUSTED,
                f"Requested {quantity} of '{item_id}', but only {in_stock} in stock.",
            )

        logger.info("CheckInventory OK: %d x %s (in stock: %d)", quantity, item_id, in_stock)
        return inventory_pb2.CheckInventoryResponse(
            available=True,
            item_id=item_id,
            requested_quantity=quantity,
            available_quantity=in_stock,
            message=f"{quantity} unit(s) of '{item_id}' available.",
        )

    def HealthCheck(self, request, context):
        return inventory_pb2.HealthCheckResponse(
            status="SERVING",
            service="inventory-grpc",
            pid=os.getpid(),
            uptime_seconds=round(time.time() - self.start_time, 1),
            delay_seconds=self.delay_seconds,
        )


def serve(port, delay_seconds):
    # A thread pool lets the server handle several RPCs at once, so a slow
    # CheckInventory does not block HealthCheck.
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    inventory_pb2_grpc.add_InventoryServiceServicer_to_server(
        InventoryServicer(delay_seconds), server
    )
    # Plain-text HTTP/2 on localhost. Production would use add_secure_port + TLS.
    server.add_insecure_port(f"127.0.0.1:{port}")
    server.start()
    logger.info(
        "gRPC Inventory Service listening on 127.0.0.1:%d (delay=%.1fs, pid=%d)",
        port,
        delay_seconds,
        os.getpid(),
    )

    def shutdown(signum, _frame):
        logger.info("Received signal %d, shutting down", signum)
        server.stop(grace=1)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    server.wait_for_termination()


def parse_args():
    parser = argparse.ArgumentParser(description="gRPC Inventory Service")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Seconds to sleep in CheckInventory (default: $INVENTORY_DELAY_SECONDS or 0).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    serve(args.port, max(0.0, args.delay))
