"""End-to-end test runner for the REST and gRPC Order -> Inventory demos.

Starts the real services as subprocesses, exercises every scenario, and
verifies that the Order side keeps running (same PID) after timeouts and
deadline failures.

Run from the project root with the virtualenv active:
    python tests/run_all_tests.py

Requires ports 5001, 5050 and 50051 to be free.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
LOG_DIR = ROOT / "tests" / "logs"

INVENTORY_REST_PORT = 5001
ORDER_REST_PORT = 5050
INVENTORY_GRPC_PORT = 50051
ORDER_URL = f"http://127.0.0.1:{ORDER_REST_PORT}"
INVENTORY_URL = f"http://127.0.0.1:{INVENTORY_REST_PORT}"

TIMEOUT_SECONDS = 2.0  # Order -> Inventory REST timeout and gRPC deadline
DELAY_SECONDS = 5.0  # Inventory delay mode (longer than the timeout/deadline)

results = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def check(name, condition, detail=""):
    results.append((name, bool(condition)))
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_process(name, args, env_extra=None, **popen_kwargs):
    env = dict(os.environ, PYTHONUNBUFFERED="1", **(env_extra or {}))
    log_file = open(LOG_DIR / f"{name}.log", "a")
    return subprocess.Popen(
        [PYTHON, *args], cwd=ROOT, env=env, stderr=log_file,
        stdout=popen_kwargs.pop("stdout", log_file), **popen_kwargs,
    )


def stop_process(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def wait_for_http(url, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=0.5).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not become healthy")


def wait_for_port(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_in_use(port):
            return
        time.sleep(0.2)
    raise RuntimeError(f"port {port} did not open")


def wait_for_port_closed(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_in_use(port):
            return
        time.sleep(0.2)
    raise RuntimeError(f"port {port} still open")


def post_order(body):
    print(f"\n  POST {ORDER_URL}/orders  {json.dumps(body)}")
    started = time.monotonic()
    response = requests.post(f"{ORDER_URL}/orders", json=body, timeout=10)
    elapsed = time.monotonic() - started
    print(f"    HTTP {response.status_code} in {elapsed:.2f}s  {response.text.strip()}")
    return response, elapsed


def order_health():
    response = requests.get(f"{ORDER_URL}/health", timeout=2)
    print(f"\n  GET {ORDER_URL}/health")
    print(f"    HTTP {response.status_code}  {response.text.strip()}")
    return response


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------
def start_rest_inventory(delay):
    process = start_process(
        "rest_inventory", ["rest/inventory_service.py", "--delay", str(delay)]
    )
    wait_for_http(f"{INVENTORY_URL}/health")
    return process


def run_rest_tests():
    print("\n" + "=" * 70 + "\nREST: Order (5050) -> Inventory (5001)\n" + "=" * 70)
    inventory = start_rest_inventory(delay=0)
    order = start_process(
        "rest_order",
        ["rest/order_service.py"],
        env_extra={"INVENTORY_TIMEOUT_SECONDS": str(TIMEOUT_SECONDS)},
    )
    try:
        wait_for_http(f"{ORDER_URL}/health")
        order_pid = order_health().json()["pid"]

        print("\n-- Normal Inventory --")
        response, _ = post_order({"item_id": "item-1", "quantity": 2})
        check("REST successful order -> 200", response.status_code == 200
              and response.json()["status"] == "confirmed")

        response, _ = post_order({"item_id": "item-999", "quantity": 1})
        check("REST unknown item -> 404", response.status_code == 404
              and response.json()["error"] == "ITEM_NOT_FOUND")

        response, _ = post_order({"item_id": "item-2", "quantity": 50})
        check("REST insufficient quantity -> 409", response.status_code == 409
              and response.json()["error"] == "INSUFFICIENT_INVENTORY")

        response, _ = post_order({"item_id": "item-1", "quantity": -3})
        check("REST invalid quantity -> 400", response.status_code == 400)

        response, _ = post_order({"quantity": 1})
        check("REST missing item_id -> 400", response.status_code == 400)

        print(f"\n-- Restarting REST Inventory with --delay {DELAY_SECONDS} "
              f"(> {TIMEOUT_SECONDS}s timeout) --")
        stop_process(inventory)
        wait_for_port_closed(INVENTORY_REST_PORT)
        inventory = start_rest_inventory(delay=DELAY_SECONDS)

        response, elapsed = post_order({"item_id": "item-1", "quantity": 1})
        check("REST timeout -> 504", response.status_code == 504
              and response.json()["error"] == "INVENTORY_TIMEOUT")
        check("REST timeout returned promptly (~timeout, not delay)",
              elapsed < DELAY_SECONDS - 1, f"{elapsed:.2f}s")

        health = order_health()
        check("REST Order Service healthy after timeout (same PID)",
              health.status_code == 200 and health.json()["pid"] == order_pid,
              f"pid={health.json()['pid']}")

        print("\n-- Stopping REST Inventory entirely --")
        stop_process(inventory)
        wait_for_port_closed(INVENTORY_REST_PORT)
        response, _ = post_order({"item_id": "item-1", "quantity": 1})
        check("REST Inventory down -> 503", response.status_code == 503)

        print("\n-- Restarting REST Inventory in normal mode --")
        inventory = start_rest_inventory(delay=0)
        response, _ = post_order({"item_id": "item-1", "quantity": 1})
        health = order_health()
        check("REST Order Service serves orders again after failures (same PID)",
              response.status_code == 200 and health.json()["pid"] == order_pid
              and order.poll() is None)
    finally:
        stop_process(order)
        stop_process(inventory)


# ---------------------------------------------------------------------------
# gRPC
# ---------------------------------------------------------------------------
class InteractiveOrderClient:
    """Drives `order_client.py --interactive` - a single long-lived Order process."""

    def __init__(self):
        self.process = start_process(
            "grpc_order_client",
            ["grpc_service/order_client.py", "--interactive",
             "--deadline", str(TIMEOUT_SECONDS)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        banner = self.process.stdout.readline().strip()
        self.process.stdout.readline()  # usage hint line
        print(f"  {banner}")

    def send(self, command):
        """Send one command and return the parsed JSON result it prints."""
        print(f"\n  order> {command}")
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        header = self.process.stdout.readline().rstrip()
        print(f"    {header}")
        json_lines = []
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("order client exited unexpectedly")
            json_lines.append(line)
            print(f"    {line.rstrip()}")
            if line.rstrip() == "}":
                break
        return json.loads("".join(json_lines))

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.write("quit\n")
            self.process.stdin.flush()
            self.process.wait(timeout=5)


def start_grpc_inventory(delay):
    process = start_process(
        "grpc_inventory", ["grpc_service/inventory_server.py", "--delay", str(delay)]
    )
    wait_for_port(INVENTORY_GRPC_PORT)
    return process


def run_grpc_tests():
    print("\n" + "=" * 70 + "\ngRPC: Order client -> Inventory (50051)\n" + "=" * 70)
    inventory = start_grpc_inventory(delay=0)
    client = InteractiveOrderClient()
    client_pid = client.process.pid
    try:
        print("\n-- Normal Inventory --")
        result = client.send("item-1 2")
        check("gRPC successful order -> OK", result["grpc_status"] == "OK")

        result = client.send("item-999 1")
        check("gRPC unknown item -> NOT_FOUND", result["grpc_status"] == "NOT_FOUND")

        result = client.send("item-2 50")
        check("gRPC insufficient quantity -> RESOURCE_EXHAUSTED",
              result["grpc_status"] == "RESOURCE_EXHAUSTED")

        result = client.send("item-1 0")
        check("gRPC invalid quantity -> INVALID_ARGUMENT",
              result["grpc_status"] == "INVALID_ARGUMENT")

        result = client.send("bad!id 1")
        check("gRPC invalid item_id -> INVALID_ARGUMENT",
              result["grpc_status"] == "INVALID_ARGUMENT")

        print(f"\n-- Restarting gRPC Inventory with --delay {DELAY_SECONDS} "
              f"(> {TIMEOUT_SECONDS}s deadline) --")
        stop_process(inventory)
        wait_for_port_closed(INVENTORY_GRPC_PORT)
        inventory = start_grpc_inventory(delay=DELAY_SECONDS)

        result = client.send("item-1 1")
        check("gRPC deadline -> DEADLINE_EXCEEDED", result["grpc_status"] == "DEADLINE_EXCEEDED")
        check("gRPC deadline enforced (~deadline, not delay)",
              result["elapsed_seconds"] < DELAY_SECONDS - 1, f"{result['elapsed_seconds']}s")

        result = client.send("health")
        check("gRPC Order client alive after deadline; Inventory HealthCheck SERVING",
              client.process.poll() is None and result["status"] == "SERVING")

        print("\n-- Stopping gRPC Inventory entirely --")
        stop_process(inventory)
        wait_for_port_closed(INVENTORY_GRPC_PORT)
        result = client.send("item-1 1")
        check("gRPC Inventory down -> UNAVAILABLE", result["grpc_status"] == "UNAVAILABLE")

        print("\n-- Restarting gRPC Inventory in normal mode --")
        inventory = start_grpc_inventory(delay=0)
        # The client's channel reconnects with backoff after the outage, so
        # poll HealthCheck until it is SERVING again (usually <= 1s).
        for _ in range(10):
            if client.send("health")["status"] == "SERVING":
                break
            time.sleep(0.5)
        result = client.send("item-1 1")
        check("gRPC Order client serves orders again after failures (same PID)",
              result["grpc_status"] == "OK" and client.process.pid == client_pid
              and client.process.poll() is None, f"pid={client_pid}")
    finally:
        client.close()
        stop_process(inventory)


def main():
    LOG_DIR.mkdir(exist_ok=True)
    busy = [p for p in (INVENTORY_REST_PORT, ORDER_REST_PORT, INVENTORY_GRPC_PORT) if port_in_use(p)]
    if busy:
        print(f"Ports already in use: {busy}. Stop the running services first.")
        return 2

    run_rest_tests()
    run_grpc_tests()

    passed = sum(1 for _, ok in results if ok)
    print("\n" + "=" * 70)
    print(f"SUMMARY: {passed}/{len(results)} checks passed")
    for name, ok in results:
        if not ok:
            print(f"  FAILED: {name}")
    print(f"Service logs: {LOG_DIR}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
