"""gRPC Order client flow.

Places an order by calling InventoryService.CheckInventory over gRPC with a
deadline, and turns every possible gRPC outcome into a clean result instead
of crashing.

Usage (from the project root):
    # One order
    python grpc_service/order_client.py --item-id item-1 --quantity 2

    # Liveness check of the Inventory server (never delayed)
    python grpc_service/order_client.py --health

    # Long-running Order process: type orders one per line
    python grpc_service/order_client.py --interactive
        > item-1 2
        > health
        > quit

    # Scripted run of all scenarios in ONE process
    python grpc_service/order_client.py --demo

Configuration (environment variables or flags)
    INVENTORY_GRPC_TARGET          default 127.0.0.1:50051   (--target)
    ORDER_GRPC_DEADLINE_SECONDS    default 2.0               (--deadline)
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).resolve().parent / "generated"))
import inventory_pb2  # noqa: E402
import inventory_pb2_grpc  # noqa: E402

DEFAULT_TARGET = os.environ.get("INVENTORY_GRPC_TARGET", "127.0.0.1:50051")
DEFAULT_DEADLINE_SECONDS = float(os.environ.get("ORDER_GRPC_DEADLINE_SECONDS", "2.0"))
HEALTH_DEADLINE_SECONDS = 1.0


class GrpcOrderClient:
    """Order-side logic. One long-lived channel is reused for every call."""

    def __init__(self, target, deadline_seconds):
        self.target = target
        self.deadline_seconds = deadline_seconds
        # A channel is a (lazy) HTTP/2 connection to the server. Creating it
        # does not contact the server; the first RPC does.
        #
        # Unlike `requests`, a channel is stateful: if the server goes down,
        # the channel enters TRANSIENT_FAILURE and reconnects with exponential
        # backoff (default up to 120s), failing calls fast with UNAVAILABLE
        # meanwhile. We cap the backoff so the client recovers quickly once
        # the Inventory server is back.
        self.channel = grpc.insecure_channel(
            target,
            options=[
                ("grpc.initial_reconnect_backoff_ms", 200),
                ("grpc.min_reconnect_backoff_ms", 200),
                ("grpc.max_reconnect_backoff_ms", 1000),
            ],
        )
        # The stub is the generated, strongly-typed client: calling
        # stub.CheckInventory(...) looks like a local function call.
        self.stub = inventory_pb2_grpc.InventoryServiceStub(self.channel)

    def place_order(self, item_id, quantity):
        """Return a result dict. Never raises - failures become 'rejected'/'failed' results."""
        order_id = f"ord-{uuid.uuid4().hex[:8]}"
        request = inventory_pb2.CheckInventoryRequest(item_id=item_id, quantity=quantity)
        started = time.monotonic()
        try:
            # `timeout=` sets the gRPC DEADLINE. It is sent to the server with
            # the request, and if no response arrives in time the client
            # library cancels the call and raises RpcError(DEADLINE_EXCEEDED).
            response = self.stub.CheckInventory(request, timeout=self.deadline_seconds)
        except grpc.RpcError as rpc_error:
            # Every gRPC failure carries a status code + details string.
            code = rpc_error.code()
            return {
                "status": "failed" if code in _INFRA_FAILURES else "rejected",
                "order_id": order_id,
                "grpc_status": code.name,
                "message": rpc_error.details(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        except Exception as exc:  # noqa: BLE001 - last-resort guard, keep the process alive
            return {
                "status": "failed",
                "order_id": order_id,
                "grpc_status": "UNKNOWN",
                "message": f"Unexpected client error: {exc!r}",
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }

        return {
            "status": "confirmed",
            "order_id": order_id,
            "grpc_status": "OK",
            "item_id": response.item_id,
            "quantity": response.requested_quantity,
            "inventory": {
                "available": response.available,
                "available_quantity": response.available_quantity,
                "message": response.message,
            },
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }

    def health(self):
        try:
            response = self.stub.HealthCheck(
                inventory_pb2.HealthCheckRequest(), timeout=HEALTH_DEADLINE_SECONDS
            )
        except grpc.RpcError as rpc_error:
            return {
                "status": "unreachable",
                "grpc_status": rpc_error.code().name,
                "message": rpc_error.details(),
            }
        return {
            "status": response.status,
            "service": response.service,
            "pid": response.pid,
            "uptime_seconds": round(response.uptime_seconds, 1),
            "delay_seconds": response.delay_seconds,
        }

    def close(self):
        self.channel.close()


# Failures caused by infrastructure rather than by the order itself.
_INFRA_FAILURES = {
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.INTERNAL,
    grpc.StatusCode.UNKNOWN,
    grpc.StatusCode.CANCELLED,
}


def print_result(label, result):
    print(f"[order-grpc pid={os.getpid()}] {label}")
    print(json.dumps(result, indent=2))
    sys.stdout.flush()


def run_single(client, item_id, quantity):
    result = client.place_order(item_id, quantity)
    print_result(
        f"CheckInventory(item_id={item_id!r}, quantity={quantity}) "
        f"deadline={client.deadline_seconds}s",
        result,
    )
    return 0 if result["status"] == "confirmed" else 1


def run_interactive(client):
    """Long-running Order process: proves it keeps working after failures."""
    print(
        f"gRPC Order client (pid={os.getpid()}) -> {client.target}, "
        f"deadline={client.deadline_seconds}s"
    )
    print("Enter '<item_id> <quantity>', 'health', or 'quit'.")
    sys.stdout.flush()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        if line in ("quit", "exit"):
            break
        if line == "health":
            print_result("Order client alive; Inventory HealthCheck:", client.health())
            continue
        parts = line.split()
        if len(parts) != 2:
            print("Expected '<item_id> <quantity>'")
            sys.stdout.flush()
            continue
        try:
            quantity = int(parts[1])
        except ValueError:
            print("quantity must be an integer")
            sys.stdout.flush()
            continue
        run_single(client, parts[0], quantity)
    print(f"gRPC Order client (pid={os.getpid()}) exiting normally.")
    return 0


def run_demo(client):
    """Run every scenario in one process. Use with a normal OR a slow server."""
    scenarios = [
        ("item-1", 2, "successful order"),
        ("item-999", 1, "unknown item -> NOT_FOUND"),
        ("item-2", 50, "insufficient stock -> RESOURCE_EXHAUSTED"),
        ("", 1, "invalid input -> INVALID_ARGUMENT"),
        ("item-1", 0, "invalid input -> INVALID_ARGUMENT"),
    ]
    print_result("Inventory HealthCheck (before):", client.health())
    for item_id, quantity, description in scenarios:
        print(f"\n=== {description} ===")
        run_single(client, item_id, quantity)
    print("\n=== same process, after all calls ===")
    print_result("Order client still running; Inventory HealthCheck (after):", client.health())
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="gRPC Order client")
    parser.add_argument("--target", default=DEFAULT_TARGET, help="host:port of Inventory server")
    parser.add_argument(
        "--deadline",
        type=float,
        default=DEFAULT_DEADLINE_SECONDS,
        help="gRPC deadline in seconds for CheckInventory (default 2.0)",
    )
    parser.add_argument("--item-id")
    parser.add_argument("--quantity", type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--health", action="store_true", help="call HealthCheck and exit")
    mode.add_argument("--interactive", action="store_true", help="read orders from stdin")
    mode.add_argument("--demo", action="store_true", help="run all scenarios in one process")
    args = parser.parse_args()
    if not (args.health or args.interactive or args.demo):
        if args.item_id is None or args.quantity is None:
            parser.error("--item-id and --quantity are required (or use --health/--interactive/--demo)")
    return args


def main():
    args = parse_args()
    client = GrpcOrderClient(args.target, args.deadline)
    try:
        if args.health:
            result = client.health()
            print_result("Inventory HealthCheck:", result)
            return 0 if result["status"] == "SERVING" else 1
        if args.interactive:
            return run_interactive(client)
        if args.demo:
            return run_demo(client)
        return run_single(client, args.item_id, args.quantity)
    except KeyboardInterrupt:
        return 130
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
