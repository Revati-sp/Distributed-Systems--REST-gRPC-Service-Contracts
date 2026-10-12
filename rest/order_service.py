"""REST Order Service (Flask, JSON over HTTP).

Accepts an order and asks the REST Inventory Service whether it can be filled.

Endpoints
---------
GET  /health   -> 200 {"status": "ok", "pid": ...}
POST /orders   -> body {"item_id": "item-1", "quantity": 2}
    200 order confirmed (inventory available)
    400 invalid input
    404 unknown item
    409 insufficient inventory
    503 Inventory Service unreachable (connection refused)
    504 Inventory Service did not answer within the timeout
    500 unexpected error

Configuration (environment variables)
-------------------------------------
ORDER_SERVICE_PORT          default 5050 (5000 is used by macOS AirPlay Receiver)
INVENTORY_REST_URL          default http://127.0.0.1:5001
INVENTORY_TIMEOUT_SECONDS   default 2.0  (read timeout for the inventory call)
"""

import argparse
import logging
import os
import re
import time
import uuid

import requests
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

DEFAULT_PORT = int(os.environ.get("ORDER_SERVICE_PORT", "5050"))
INVENTORY_REST_URL = os.environ.get("INVENTORY_REST_URL", "http://127.0.0.1:5001").rstrip("/")
INVENTORY_TIMEOUT_SECONDS = float(os.environ.get("INVENTORY_TIMEOUT_SECONDS", "2.0"))
CONNECT_TIMEOUT_SECONDS = 1.0

ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_QUANTITY = 1_000_000

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [order-rest] %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
START_TIME = time.time()

# A Session reuses TCP connections to the Inventory Service between requests.
http_session = requests.Session()


def error_response(http_status, error_code, message, **extra):
    body = {"status": "rejected", "error": error_code, "message": message}
    body.update(extra)
    return jsonify(body), http_status


def validate_order(payload):
    """Validate input locally first so we don't waste a network call on bad data."""
    if not isinstance(payload, dict):
        return None, None, "Request body must be a JSON object."

    item_id = payload.get("item_id")
    quantity = payload.get("quantity")

    if not isinstance(item_id, str) or not item_id.strip():
        return None, None, "'item_id' is required and must be a non-empty string."
    if not ITEM_ID_PATTERN.match(item_id):
        return None, None, "'item_id' may only contain letters, digits, '-' or '_' (max 64 chars)."
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        return None, None, "'quantity' is required and must be an integer."
    if quantity <= 0:
        return None, None, "'quantity' must be greater than 0."
    if quantity > MAX_QUANTITY:
        return None, None, f"'quantity' must not exceed {MAX_QUANTITY}."
    return item_id, quantity, None


@app.get("/health")
def health():
    """Liveness probe: answers instantly, independent of the Inventory Service."""
    return jsonify(
        {
            "status": "ok",
            "service": "order-rest",
            "pid": os.getpid(),
            "uptime_seconds": round(time.time() - START_TIME, 1),
            "inventory_url": INVENTORY_REST_URL,
            "inventory_timeout_seconds": INVENTORY_TIMEOUT_SECONDS,
        }
    ), 200


@app.post("/orders")
def create_order():
    payload = request.get_json(silent=True)
    item_id, quantity, validation_error = validate_order(payload)
    if validation_error:
        return error_response(400, "INVALID_ARGUMENT", validation_error)

    order_id = f"ord-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()

    try:
        # IMPORTANT: always set a timeout on outbound HTTP calls. Without one,
        # `requests` waits forever and a slow Inventory Service would tie up
        # this worker indefinitely. The tuple is (connect timeout, read timeout).
        inventory_response = http_session.post(
            f"{INVENTORY_REST_URL}/inventory/check",
            json={"item_id": item_id, "quantity": quantity},
            timeout=(CONNECT_TIMEOUT_SECONDS, INVENTORY_TIMEOUT_SECONDS),
        )
    except requests.exceptions.Timeout:
        elapsed = time.monotonic() - started
        logger.warning("Order %s: Inventory timed out after %.2fs", order_id, elapsed)
        # 504 Gateway Timeout: *we* are healthy, but the upstream service we
        # depend on did not answer in time.
        return error_response(
            504,
            "INVENTORY_TIMEOUT",
            f"Inventory Service did not respond within {INVENTORY_TIMEOUT_SECONDS}s.",
            order_id=order_id,
        )
    except requests.exceptions.ConnectionError:
        logger.warning("Order %s: Inventory Service unreachable", order_id)
        # 503 Service Unavailable: the dependency is down / refusing connections.
        return error_response(
            503,
            "INVENTORY_UNAVAILABLE",
            f"Could not connect to Inventory Service at {INVENTORY_REST_URL}.",
            order_id=order_id,
        )
    except requests.exceptions.RequestException as exc:
        logger.exception("Order %s: HTTP error calling Inventory: %s", order_id, exc)
        return error_response(500, "INTERNAL_ERROR", "Error while calling Inventory Service.")

    # Translate the Inventory Service's HTTP status into the Order Service's answer.
    try:
        inventory_body = inventory_response.json()
    except ValueError:
        inventory_body = {}

    status = inventory_response.status_code
    if status == 200:
        logger.info("Order %s confirmed: %d x %s", order_id, quantity, item_id)
        return jsonify(
            {
                "status": "confirmed",
                "order_id": order_id,
                "item_id": item_id,
                "quantity": quantity,
                "inventory": inventory_body,
            }
        ), 200
    if status in (400, 404, 409):
        # Pass the client-side error straight through with the same status code.
        logger.info("Order %s rejected by Inventory: HTTP %d", order_id, status)
        return error_response(
            status,
            inventory_body.get("error", "INVENTORY_REJECTED"),
            inventory_body.get("message", "Inventory rejected the request."),
            order_id=order_id,
        )

    logger.error("Order %s: unexpected Inventory status %d", order_id, status)
    return error_response(
        500,
        "INTERNAL_ERROR",
        f"Inventory Service returned unexpected HTTP {status}.",
        order_id=order_id,
    )


@app.errorhandler(Exception)
def unexpected_error(error):
    if isinstance(error, HTTPException):
        return error_response(error.code, error.name.upper().replace(" ", "_"), error.description)
    logger.exception("Unexpected error: %s", error)
    return error_response(500, "INTERNAL_ERROR", "An unexpected error occurred.")


def parse_args():
    parser = argparse.ArgumentParser(description="REST Order Service")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logger.info(
        "Starting REST Order Service on http://127.0.0.1:%d -> inventory %s (timeout=%.1fs, pid=%d)",
        args.port,
        INVENTORY_REST_URL,
        INVENTORY_TIMEOUT_SECONDS,
        os.getpid(),
    )
    app.run(host="127.0.0.1", port=args.port, threaded=True)
