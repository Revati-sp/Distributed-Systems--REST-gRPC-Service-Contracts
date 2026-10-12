"""REST Inventory Service (Flask, JSON over HTTP).

Endpoints
---------
GET  /health            -> 200 {"status": "ok", ...}   (never delayed)
GET  /inventory         -> 200 {"inventory": {...}}    (convenience listing)
POST /inventory/check   -> body {"item_id": "item-1", "quantity": 2}
    200 available, 400 invalid input, 404 unknown item, 409 insufficient stock

Delay mode (to demonstrate client-side timeouts)
------------------------------------------------
    python rest/inventory_service.py --delay 5
    # or
    INVENTORY_DELAY_SECONDS=5 python rest/inventory_service.py

The delay is applied only to POST /inventory/check, so /health still answers
instantly and proves the process is alive.
"""

import argparse
import logging
import os
import re
import time

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_PORT = int(os.environ.get("INVENTORY_REST_PORT", "5001"))
DEFAULT_DELAY_SECONDS = float(os.environ.get("INVENTORY_DELAY_SECONDS", "0"))

# Simple in-memory inventory: item_id -> units in stock.
INVENTORY = {
    "item-1": 10,
    "item-2": 5,
    "item-3": 0,
}

# Allowed item id format: letters, digits, '-' and '_', 1-64 chars.
ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_QUANTITY = 1_000_000

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [inventory-rest] %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["DELAY_SECONDS"] = DEFAULT_DELAY_SECONDS
START_TIME = time.time()


def error_response(http_status, error_code, message):
    """Build a consistent JSON error body.

    REST convention: the HTTP status code tells the client the *category* of
    the outcome, while the JSON body carries machine-readable details.
    """
    return jsonify({"error": error_code, "message": message}), http_status


def validate_check_request(payload):
    """Return (item_id, quantity, None) if valid, else (None, None, error_message)."""
    if not isinstance(payload, dict):
        return None, None, "Request body must be a JSON object."

    item_id = payload.get("item_id")
    quantity = payload.get("quantity")

    if not isinstance(item_id, str) or not item_id.strip():
        return None, None, "'item_id' is required and must be a non-empty string."
    if not ITEM_ID_PATTERN.match(item_id):
        return None, None, "'item_id' may only contain letters, digits, '-' or '_' (max 64 chars)."

    # bool is a subclass of int in Python, so reject it explicitly.
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        return None, None, "'quantity' is required and must be an integer."
    if quantity <= 0:
        return None, None, "'quantity' must be greater than 0."
    if quantity > MAX_QUANTITY:
        return None, None, f"'quantity' must not exceed {MAX_QUANTITY}."

    return item_id, quantity, None


@app.get("/health")
def health():
    """Liveness probe. Deliberately NOT affected by the delay setting."""
    return jsonify(
        {
            "status": "ok",
            "service": "inventory-rest",
            "pid": os.getpid(),
            "uptime_seconds": round(time.time() - START_TIME, 1),
            "delay_seconds": app.config["DELAY_SECONDS"],
        }
    ), 200


@app.get("/inventory")
def list_inventory():
    return jsonify({"inventory": INVENTORY}), 200


@app.post("/inventory/check")
def check_inventory():
    delay_seconds = app.config["DELAY_SECONDS"]
    if delay_seconds > 0:
        # Simulate a slow dependency (e.g. a slow database). If this is longer
        # than the Order Service's HTTP timeout, the Order Service gives up
        # and returns 504 Gateway Timeout to its own caller.
        logger.info("Delay mode: sleeping %.1fs before answering", delay_seconds)
        time.sleep(delay_seconds)

    # silent=True -> returns None instead of raising on malformed JSON,
    # so we can answer with our own clean 400 response.
    payload = request.get_json(silent=True)
    item_id, quantity, validation_error = validate_check_request(payload)
    if validation_error:
        return error_response(400, "INVALID_ARGUMENT", validation_error)

    if item_id not in INVENTORY:
        return error_response(404, "ITEM_NOT_FOUND", f"Item '{item_id}' does not exist.")

    in_stock = INVENTORY[item_id]
    if quantity > in_stock:
        # 409 Conflict: the request is well-formed, but conflicts with the
        # current state of the resource (not enough stock).
        return jsonify(
            {
                "error": "INSUFFICIENT_INVENTORY",
                "message": f"Requested {quantity} of '{item_id}', but only {in_stock} in stock.",
                "item_id": item_id,
                "requested_quantity": quantity,
                "available_quantity": in_stock,
            }
        ), 409

    return jsonify(
        {
            "available": True,
            "item_id": item_id,
            "requested_quantity": quantity,
            "available_quantity": in_stock,
            "message": f"{quantity} unit(s) of '{item_id}' available.",
        }
    ), 200


@app.errorhandler(404)
def unknown_route(_error):
    return error_response(404, "NOT_FOUND", "The requested URL was not found.")


@app.errorhandler(405)
def method_not_allowed(_error):
    return error_response(405, "METHOD_NOT_ALLOWED", "HTTP method not allowed for this URL.")


@app.errorhandler(Exception)
def unexpected_error(error):
    if isinstance(error, HTTPException):
        return error_response(error.code, error.name.upper().replace(" ", "_"), error.description)
    # Any bug or unexpected failure becomes a clean 500 instead of an HTML page.
    logger.exception("Unexpected error: %s", error)
    return error_response(500, "INTERNAL_ERROR", "An unexpected error occurred.")


def parse_args():
    parser = argparse.ArgumentParser(description="REST Inventory Service")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Seconds to sleep before answering /inventory/check (default: "
        "$INVENTORY_DELAY_SECONDS or 0).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.config["DELAY_SECONDS"] = max(0.0, args.delay)
    logger.info(
        "Starting REST Inventory Service on http://127.0.0.1:%d (delay=%.1fs, pid=%d)",
        args.port,
        app.config["DELAY_SECONDS"],
        os.getpid(),
    )
    # threaded=True: each request runs in its own thread, so a slow
    # /inventory/check does not block /health.
    app.run(host="127.0.0.1", port=args.port, threaded=True)
