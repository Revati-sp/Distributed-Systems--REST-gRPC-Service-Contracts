# Order → Inventory: REST vs gRPC Service Contracts

This project implements one service interaction twice in Python, once over **REST/HTTP (JSON)** and once over **gRPC (Protocol Buffers)**. An **Order Service** receives an order (`item_id` + `quantity`) and asks an **Inventory Service** whether that many units are in stock.

Both versions use the same inventory data, the same validation rules and a timeout/deadline of **2.0 seconds**. Both Inventory services have a **delay mode** that makes them slower than that limit. With delay mode on, you can watch the Order side fail cleanly with a timeout and keep running afterwards.

| | REST version | gRPC version |
|---|---|---|
| Inventory Service | [`rest/inventory_service.py`](rest/inventory_service.py) on port **5001** | [`grpc_service/inventory_server.py`](grpc_service/inventory_server.py) on port **50051** |
| Order side | [`rest/order_service.py`](rest/order_service.py), an HTTP API on port **5050** | [`grpc_service/order_client.py`](grpc_service/order_client.py), a gRPC client flow |
| Contract | URL paths + JSON bodies + HTTP status codes | [`grpc_service/inventory.proto`](grpc_service/inventory.proto) |
| Order → Inventory limit | HTTP **timeout** 2.0 s → `504 Gateway Timeout` | gRPC **deadline** 2.0 s → `DEADLINE_EXCEEDED` |
| Slow mode | `--delay 5` or `INVENTORY_DELAY_SECONDS=5` | `--delay 5` or `INVENTORY_DELAY_SECONDS=5` |

---

## Table of contents

1. [Project overview](#1-project-overview)
2. [Project structure](#2-project-structure)
3. [Prerequisites and installation](#3-prerequisites-and-installation)
4. [Running the REST version](#4-running-the-rest-version)
5. [Running the gRPC version](#5-running-the-grpc-version)
6. [Successful REST example](#6-successful-rest-example)
7. [Successful gRPC example](#7-successful-grpc-example)
8. [Failure examples](#8-failure-examples)
9. [REST timeout evidence](#9-rest-timeout-evidence)
10. [gRPC deadline evidence](#10-grpc-deadline-evidence)
11. [Evidence that the Order Service keeps running](#11-evidence-that-the-order-service-keeps-running)
12. [REST vs gRPC comparison](#12-rest-vs-grpc-comparison)
13. [Automated test script](#13-automated-test-script)
14. [Testing checklist](#14-testing-checklist)
15. [Screenshot guide](#15-screenshot-guide)

> **About the outputs in this README:** every terminal block is real output, copied from running these exact commands on macOS with Python 3.12.13. When you run them yourself, values such as `order_id`, `pid`, `Date` and timings will be different.

---

## 1. Project overview

**Order Service.** It accepts an order containing an `item_id` (a string) and a `quantity` (an integer). It validates the order, asks the Inventory Service whether it can be filled, and returns the result. It enforces a 2.0 s limit on that call, and it **must keep running** when the Inventory Service is slow, down, or returns an error.

**Inventory Service.** It holds a small in-memory inventory and checks whether the requested quantity of an item is available:

| `item_id` | Units in stock |
|---|---|
| `item-1` | 10 |
| `item-2` | 5 |
| `item-3` | 0 |

**Validation rules** (identical in both versions):
- `item_id` must be a non-empty string of letters, digits, `-` or `_`, at most 64 characters.
- `quantity` must be an integer from 1 to 1,000,000.

**How each outcome is reported:**

| Outcome | REST: HTTP status from the Order Service | gRPC: status code |
|---|---|---|
| Item available | `200 OK` | `OK` |
| Invalid input | `400 Bad Request` | `INVALID_ARGUMENT` |
| Unknown item | `404 Not Found` | `NOT_FOUND` |
| Not enough stock | `409 Conflict` | `RESOURCE_EXHAUSTED` |
| Inventory too slow | `504 Gateway Timeout` | `DEADLINE_EXCEEDED` |
| Inventory not running | `503 Service Unavailable` | `UNAVAILABLE` |
| Unexpected error | `500 Internal Server Error` | `UNKNOWN` / `INTERNAL` |

**Ports**

| Service | Address |
|---|---|
| REST Inventory Service | `http://127.0.0.1:5001` |
| REST Order Service | `http://127.0.0.1:5050` |
| gRPC Inventory Service | `127.0.0.1:50051` |

> **Why 5050 instead of 5000?** On macOS 12 and later, port 5000 is already used by the AirPlay Receiver (`ControlCenter`), so requests to `localhost:5000` reach Apple's service instead of this project. If port 5000 is free on your machine, you can still use it with `ORDER_SERVICE_PORT=5000 python rest/order_service.py`.

---

## 2. Project structure

```text
.
├── .gitignore
├── README.md
├── requirements.txt               # pinned dependencies (Flask, requests, grpcio, grpcio-tools, protobuf)
├── rest/                          # ── REST implementation ──
│   ├── inventory_service.py       # Flask: POST /inventory/check, GET /inventory, GET /health  (port 5001)
│   └── order_service.py           # Flask: POST /orders, GET /health  (port 5050), 2.0 s timeout
├── grpc_service/                  # ── gRPC implementation ──
│   ├── inventory.proto            # the contract: InventoryService.CheckInventory + HealthCheck
│   ├── generate_code.py           # runs protoc to (re)create generated/
│   ├── inventory_server.py        # gRPC Inventory server  (port 50051)
│   ├── order_client.py            # gRPC Order client flow, 2.0 s deadline
│   └── generated/
│       ├── inventory_pb2.py       # generated message classes
│       └── inventory_pb2_grpc.py  # generated client stub + server base class
├── tests/
│   ├── run_all_tests.py           # end-to-end test runner (20 checks)
│   └── logs/                      # service logs written by the test runner (git-ignored)
└── docs/
    └── test_run_output.txt        # full output of a real run of tests/run_all_tests.py
```

The gRPC folder is named `grpc_service/` rather than `grpc/` on purpose. A local folder called `grpc` can shadow the installed `grpc` library, so `import grpc` would load the wrong thing. The `.venv/` virtual-environment folder is created during installation and is git-ignored.

---

## 3. Prerequisites and installation

### Prerequisites

| Requirement | Details |
|---|---|
| **Python 3.10 or newer** | Tested with **Python 3.12.13**. The pinned `grpcio 1.84.0`, `protobuf 7.36.2` and `requests 2.34.2` do **not** install on Python 3.9, which is the version of `/usr/bin/python3` on many Macs. Check with `python3 --version`. On macOS, `brew install python@3.12` gives you a `python3.12` command. |
| `curl` | Used for the REST examples. It is preinstalled on macOS and Linux. |
| Free ports | **5001**, **5050** and **50051** |

### 1) Create and activate a virtual environment

```bash
cd "REST & gRPC Service Contracts"     # the project root
python3.12 -m venv .venv              # any Python 3.10+ works
source .venv/bin/activate             # Windows (PowerShell): .venv\Scripts\Activate.ps1
```

### 2) Install the dependencies

```bash
pip install -r requirements.txt
```

### 3) Generate the gRPC Python code

```bash
python grpc_service/generate_code.py
```

```text
Generated gRPC code in .../grpc_service/generated
```

This compiles `grpc_service/inventory.proto` into `grpc_service/generated/inventory_pb2.py` and `inventory_pb2_grpc.py`. Those two files are already committed, so this step is only *required* after you edit the `.proto` file or change the `grpcio-tools` version. Running it again is always safe. The script is equivalent to this `protoc` command:

```bash
python -m grpc_tools.protoc -I grpc_service \
    --python_out=grpc_service/generated \
    --grpc_python_out=grpc_service/generated \
    grpc_service/inventory.proto
```

> **Every command in the rest of this README** assumes the virtual environment is active and you are in the project root. For each terminal you open, `cd` into the project root and run `source .venv/bin/activate`.

---

## 4. Running the REST version

You need **three terminals**.

**Terminal 1: start the REST Inventory Service on port 5001**

```bash
python rest/inventory_service.py
```

```text
2026-10-11 14:38:38,207 [inventory-rest] INFO Starting REST Inventory Service on http://127.0.0.1:5001 (delay=0.0s, pid=39515)
 * Running on http://127.0.0.1:5001
```

**Terminal 2: start the REST Order Service on port 5050**

```bash
python rest/order_service.py
```

```text
2026-10-11 14:38:38,207 [order-rest] INFO Starting REST Order Service on http://127.0.0.1:5050 -> inventory http://127.0.0.1:5001 (timeout=2.0s, pid=39516)
 * Running on http://127.0.0.1:5050
```

**Terminal 3: check that both services are up**

```bash
curl -s http://127.0.0.1:5050/health
curl -s http://127.0.0.1:5001/health
```

```text
{"inventory_timeout_seconds":2.0,"inventory_url":"http://127.0.0.1:5001","pid":39516,"service":"order-rest","status":"ok","uptime_seconds":0.0}
{"delay_seconds":0.0,"pid":39515,"service":"inventory-rest","status":"ok","uptime_seconds":0.1}
```

### REST endpoints

| Service | Method and path | Purpose |
|---|---|---|
| Order (5050) | `POST /orders` | Place an order. Body: `{"item_id": "item-1", "quantity": 2}` |
| Order (5050) | `GET /health` | Liveness check. Includes the process `pid` and the configured timeout. |
| Inventory (5001) | `POST /inventory/check` | Check availability; this is what the Order Service calls. Same body format. |
| Inventory (5001) | `GET /inventory` | List the current stock. |
| Inventory (5001) | `GET /health` | Liveness check. **Never delayed**, even in delay mode. |

### REST configuration

| Environment variable | Default | Used by | Meaning |
|---|---|---|---|
| `INVENTORY_TIMEOUT_SECONDS` | `2.0` | Order | Read timeout for the Order → Inventory HTTP call |
| `INVENTORY_REST_URL` | `http://127.0.0.1:5001` | Order | Where the Inventory Service is |
| `ORDER_SERVICE_PORT` | `5050` | Order | Port of the Order Service (`--port` also works) |
| `INVENTORY_REST_PORT` | `5001` | Inventory | Port of the Inventory Service (`--port` also works) |
| `INVENTORY_DELAY_SECONDS` | `0` | Inventory | Artificial delay; same as `--delay` |

Stop any service with `Ctrl+C`.

---

## 5. Running the gRPC version

You need **two terminals**.

**Step 1: generate the Python files from the `.proto`.** This is already done if you followed [section 3](#3-prerequisites-and-installation).

```bash
python grpc_service/generate_code.py
```

The contract being compiled ([`grpc_service/inventory.proto`](grpc_service/inventory.proto)):

```protobuf
service InventoryService {
  rpc CheckInventory (CheckInventoryRequest) returns (CheckInventoryResponse);
  rpc HealthCheck (HealthCheckRequest) returns (HealthCheckResponse);
}

message CheckInventoryRequest {
  string item_id = 1;
  int32 quantity = 2;
}

message CheckInventoryResponse {
  bool available = 1;
  string item_id = 2;
  int32 requested_quantity = 3;
  int32 available_quantity = 4;
  string message = 5;
}
```

**Step 2 (Terminal 1): start the gRPC Inventory Service on port 50051**

```bash
python grpc_service/inventory_server.py
```

```text
2026-10-11 14:38:53,436 [inventory-grpc] INFO gRPC Inventory Service listening on 127.0.0.1:50051 (delay=0.0s, pid=39580)
```

**Step 3 (Terminal 2): run the gRPC Order Client**

```bash
python grpc_service/order_client.py --item-id item-1 --quantity 2   # place one order, then exit
python grpc_service/order_client.py --health                        # call the server's HealthCheck RPC
python grpc_service/order_client.py --interactive                   # stay running and read orders from the keyboard
python grpc_service/order_client.py --demo                          # run every scenario in one process
```

| Client mode | What it does |
|---|---|
| `--item-id X --quantity N` | Sends one `CheckInventory` RPC and prints the result. Exit code `0` = confirmed, `1` = rejected or failed. |
| `--health` | Calls `HealthCheck` (deadline 1.0 s). It answers instantly even in delay mode. |
| `--interactive` | A **long-running** Order process. Type `item-1 2` to order, `health` to check the server, or `quit` to exit. |
| `--demo` | Runs success, not-found, insufficient and invalid orders, plus health checks, all in one process. |

### gRPC configuration

| Flag / environment variable | Default | Meaning |
|---|---|---|
| `--deadline` / `ORDER_GRPC_DEADLINE_SECONDS` | `2.0` | gRPC deadline for `CheckInventory` |
| `--target` / `INVENTORY_GRPC_TARGET` | `127.0.0.1:50051` | Address of the Inventory server |
| `--port` / `INVENTORY_GRPC_PORT` (server) | `50051` | Port of the gRPC Inventory server |
| `--delay` / `INVENTORY_DELAY_SECONDS` (server) | `0` | Artificial delay in `CheckInventory` |

---

## 6. Successful REST example

With both REST services running ([section 4](#4-running-the-rest-version)), place an order for 2 units of `item-1`:

> 📸 **Screenshot 1:** this request and its response.

```text
$ curl -si -X POST http://127.0.0.1:5050/orders -H 'Content-Type: application/json' -d '{"item_id": "item-1", "quantity": 2}'
HTTP/1.1 200 OK
Server: Werkzeug/3.1.9 Python/3.12.13
Date: Sun, 11 Oct 2026 21:38:38 GMT
Content-Type: application/json
Content-Length: 223
Connection: close

{"inventory":{"available":true,"available_quantity":10,"item_id":"item-1","message":"2 unit(s) of 'item-1' available.","requested_quantity":2},"item_id":"item-1","order_id":"ord-ad2f3b0a","quantity":2,"status":"confirmed"}
```

The same JSON body, formatted for readability:

```json
{
  "status": "confirmed",
  "order_id": "ord-ad2f3b0a",
  "item_id": "item-1",
  "quantity": 2,
  "inventory": {
    "available": true,
    "item_id": "item-1",
    "requested_quantity": 2,
    "available_quantity": 10,
    "message": "2 unit(s) of 'item-1' available."
  }
}
```

**What happened.** The Order Service validated the input. It then sent `POST /inventory/check` to the Inventory Service on port 5001, which answered `200` because 2 ≤ 10 units are in stock. The Order Service then returned **`200 OK`**, meaning the request succeeded and the order is confirmed. The `inventory` object is the Inventory Service's own response, passed through. (`-s` hides curl's progress meter; `-i` prints the status line and headers.)

---

## 7. Successful gRPC example

With the gRPC Inventory Service running ([section 5](#5-running-the-grpc-version)):

> 📸 **Screenshot 2:** this command and its output.

```text
$ python grpc_service/order_client.py --item-id item-1 --quantity 2
[order-grpc pid=39587] CheckInventory(item_id='item-1', quantity=2) deadline=2.0s
{
  "status": "confirmed",
  "order_id": "ord-76e31e67",
  "grpc_status": "OK",
  "item_id": "item-1",
  "quantity": 2,
  "inventory": {
    "available": true,
    "available_quantity": 10,
    "message": "2 unit(s) of 'item-1' available."
  },
  "elapsed_seconds": 0.002
}
```

**The request** is a Protocol Buffers message, `CheckInventoryRequest{item_id: "item-1", quantity: 2}`. It is sent in binary over HTTP/2 with a **2.0 s deadline**, which the first output line shows.

**The response** is a `CheckInventoryResponse{available: true, item_id: "item-1", requested_quantity: 2, available_quantity: 10, message: "..."}`. The client prints it as JSON, along with the order id, the gRPC status and how long the call took.

**What happened.** The RPC completed with gRPC status **`OK`**, gRPC's equivalent of HTTP 200. Unlike REST, there is no status line or headers to read: success simply means the stub call returned a typed response object instead of raising an error.

---

## 8. Failure examples

### REST failures

> 📸 **Screenshot 3:** these four requests and responses.

```text
$ curl -si -X POST http://127.0.0.1:5050/orders -H 'Content-Type: application/json' -d '{"item_id": "item-999", "quantity": 1}'
HTTP/1.1 404 NOT FOUND
Server: Werkzeug/3.1.9 Python/3.12.13
Date: Sun, 11 Oct 2026 21:38:38 GMT
Content-Type: application/json
Content-Length: 117
Connection: close

{"error":"ITEM_NOT_FOUND","message":"Item 'item-999' does not exist.","order_id":"ord-431998f6","status":"rejected"}

$ curl -si -X POST http://127.0.0.1:5050/orders -H 'Content-Type: application/json' -d '{"item_id": "item-2", "quantity": 50}'
HTTP/1.1 409 CONFLICT
Server: Werkzeug/3.1.9 Python/3.12.13
Date: Sun, 11 Oct 2026 21:38:38 GMT
Content-Type: application/json
Content-Length: 140
Connection: close

{"error":"INSUFFICIENT_INVENTORY","message":"Requested 50 of 'item-2', but only 5 in stock.","order_id":"ord-da1facbf","status":"rejected"}

$ curl -si -X POST http://127.0.0.1:5050/orders -H 'Content-Type: application/json' -d '{"item_id": "item-1", "quantity": 0}'
HTTP/1.1 400 BAD REQUEST
Server: Werkzeug/3.1.9 Python/3.12.13
Date: Sun, 11 Oct 2026 21:38:38 GMT
Content-Type: application/json
Content-Length: 96
Connection: close

{"error":"INVALID_ARGUMENT","message":"'quantity' must be greater than 0.","status":"rejected"}

$ curl -si -X POST http://127.0.0.1:5050/orders -H 'Content-Type: application/json' -d '{"item_id": "item-1", "quantity": "two"}'
HTTP/1.1 400 BAD REQUEST
Server: Werkzeug/3.1.9 Python/3.12.13
Date: Sun, 11 Oct 2026 21:38:38 GMT
Content-Type: application/json
Content-Length: 108
Connection: close

{"error":"INVALID_ARGUMENT","message":"'quantity' is required and must be an integer.","status":"rejected"}
```

| Case | Status | Why this status |
|---|---|---|
| Unknown item `item-999` | **404 Not Found** | The requested resource (the item) does not exist. The Inventory Service returned 404 and the Order Service passed it through. |
| 50 × `item-2` (only 5 in stock) | **409 Conflict** | The request is valid, but it conflicts with the current state of the inventory. |
| `quantity: 0` / `quantity: "two"` | **400 Bad Request** | The client sent invalid input. The Order Service rejects it **before** calling Inventory, which is why there is no `order_id`. |

### gRPC failures

> 📸 **Screenshot 4:** these four commands and outputs.

```text
$ python grpc_service/order_client.py --item-id item-999 --quantity 1
[order-grpc pid=39588] CheckInventory(item_id='item-999', quantity=1) deadline=2.0s
{
  "status": "rejected",
  "order_id": "ord-e8a11331",
  "grpc_status": "NOT_FOUND",
  "message": "Item 'item-999' does not exist.",
  "elapsed_seconds": 0.002
}

$ python grpc_service/order_client.py --item-id item-2 --quantity 50
[order-grpc pid=39589] CheckInventory(item_id='item-2', quantity=50) deadline=2.0s
{
  "status": "rejected",
  "order_id": "ord-c8db95de",
  "grpc_status": "RESOURCE_EXHAUSTED",
  "message": "Requested 50 of 'item-2', but only 5 in stock.",
  "elapsed_seconds": 0.002
}

$ python grpc_service/order_client.py --item-id item-1 --quantity 0
[order-grpc pid=39590] CheckInventory(item_id='item-1', quantity=0) deadline=2.0s
{
  "status": "rejected",
  "order_id": "ord-291d1682",
  "grpc_status": "INVALID_ARGUMENT",
  "message": "quantity must be greater than 0.",
  "elapsed_seconds": 0.002
}

$ python grpc_service/order_client.py --item-id 'item 1!' --quantity 1
[order-grpc pid=39591] CheckInventory(item_id='item 1!', quantity=1) deadline=2.0s
{
  "status": "rejected",
  "order_id": "ord-ef28384c",
  "grpc_status": "INVALID_ARGUMENT",
  "message": "item_id may only contain letters, digits, '-' or '_' (max 64 chars).",
  "elapsed_seconds": 0.002
}
```

| Case | gRPC status | Why this status |
|---|---|---|
| Unknown item | **`NOT_FOUND`** | The requested entity does not exist. |
| Not enough stock | **`RESOURCE_EXHAUSTED`** | The resource (stock) is insufficient for the request. |
| `quantity` 0 / malformed `item_id` | **`INVALID_ARGUMENT`** | The client sent an invalid argument. The server checks this and calls `context.abort(...)`. |

The server reports each failure with `context.abort(StatusCode, "details")`. No response message is sent. The client receives a `grpc.RpcError`, reads `.code()` and `.details()`, and prints them as `grpc_status` and `message`. Because the client catches the error, it never crashes or prints a traceback; it exits with code `1`.

---

## 9. REST timeout evidence

### The configured REST timeout

The Order Service gives the Inventory Service **2.0 seconds** to respond (from [`rest/order_service.py`](rest/order_service.py)):

```python
INVENTORY_TIMEOUT_SECONDS = float(os.environ.get("INVENTORY_TIMEOUT_SECONDS", "2.0"))
CONNECT_TIMEOUT_SECONDS = 1.0
...
inventory_response = http_session.post(
    f"{INVENTORY_REST_URL}/inventory/check",
    json={"item_id": item_id, "quantity": quantity},
    timeout=(CONNECT_TIMEOUT_SECONDS, INVENTORY_TIMEOUT_SECONDS),
)
```

The `timeout` tuple is (1.0 s to connect, 2.0 s to wait for the response). When the limit is hit, `requests` raises `requests.exceptions.Timeout`. The Order Service catches it and returns a JSON `504`. The active value appears in the Order Service's startup log (`timeout=2.0s`) and in its `/health` response (`"inventory_timeout_seconds":2.0`).

### Enabling the temporary delay (5 s, longer than the 2 s timeout)

Leave the Order Service running in **Terminal 2**. In **Terminal 1**, stop the Inventory Service with `Ctrl+C` and restart it in delay mode, using either of these:

```bash
python rest/inventory_service.py --delay 5
# or
INVENTORY_DELAY_SECONDS=5 python rest/inventory_service.py
```

```text
2026-10-11 14:38:38,456 [inventory-rest] INFO Starting REST Inventory Service on http://127.0.0.1:5001 (delay=5.0s, pid=39534)
```

The delay applies only to `POST /inventory/check`. `GET /health` still answers immediately.

### The request and the clean timeout response

In **Terminal 3** (`time` measures how long the call takes):

> 📸 **Screenshot 5:** Terminal 1 showing `delay=5.0s`, plus this request and response showing `504` and about `2.0 total`.

```text
$ time curl -si -X POST http://127.0.0.1:5050/orders -H 'Content-Type: application/json' -d '{"item_id": "item-1", "quantity": 1}'
HTTP/1.1 504 GATEWAY TIMEOUT
Server: Werkzeug/3.1.9 Python/3.12.13
Date: Sun, 11 Oct 2026 21:38:40 GMT
Content-Type: application/json
Content-Length: 135
Connection: close

{"error":"INVENTORY_TIMEOUT","message":"Inventory Service did not respond within 2.0s.","order_id":"ord-9c19c89a","status":"rejected"}
  0.00s user 0.00s system 0% cpu 2.015 total
```

Order Service log (Terminal 2) for this request:

```text
2026-10-11 14:38:40,563 [order-rest] WARNING Order ord-9c19c89a: Inventory timed out after 2.00s
2026-10-11 14:38:40,564 [order-rest] INFO 127.0.0.1 - - [11/Oct/2026 14:38:40] "POST /orders HTTP/1.1" 504 -
```

Inventory Service log (Terminal 1) for the same request:

```text
2026-10-11 14:38:38,561 [inventory-rest] INFO Delay mode: sleeping 5.0s before answering
2026-10-11 14:38:43,568 [inventory-rest] INFO 127.0.0.1 - - [11/Oct/2026 14:38:43] "POST /inventory/check HTTP/1.1" 200 -
```

**Why this counts as clean handling:**
- The caller got an answer after **2.015 s**, which is the configured timeout, not the 5 s delay.
- The answer is a well-formed JSON error with a clear code (`INVENTORY_TIMEOUT`), not a hang, a stack trace or an HTML error page.
- The status is **`504 Gateway Timeout`**: the Order Service itself is healthy, but the upstream service it depends on did not respond in time. This is different from a `500`, which would mean the Order Service itself broke.
- The Inventory log shows it kept working and answered `200` at 14:38:43, three seconds after the Order Service had stopped waiting. An HTTP timeout exists only on the client side.

---

## 10. gRPC deadline evidence

### The configured gRPC deadline

The Order client gives each `CheckInventory` call a **2.0 second deadline** (from [`grpc_service/order_client.py`](grpc_service/order_client.py)):

```python
DEFAULT_DEADLINE_SECONDS = float(os.environ.get("ORDER_GRPC_DEADLINE_SECONDS", "2.0"))
...
response = self.stub.CheckInventory(request, timeout=self.deadline_seconds)
```

In the Python gRPC API, `timeout=` sets the call's **deadline**. The deadline is sent to the server along with the request. If no response arrives in time, the client library cancels the call and raises `grpc.RpcError` with status `DEADLINE_EXCEEDED`, which the client catches. The value is printed on every call (`deadline=2.0s`) and can be changed with `--deadline` or `ORDER_GRPC_DEADLINE_SECONDS`.

### Enabling the temporary delay (5 s, longer than the 2 s deadline)

In **Terminal 1**, stop the gRPC Inventory Service with `Ctrl+C` and restart it in delay mode, using either of these:

```bash
python grpc_service/inventory_server.py --delay 5
# or
INVENTORY_DELAY_SECONDS=5 python grpc_service/inventory_server.py
```

```text
2026-10-11 14:38:55,574 [inventory-grpc] INFO gRPC Inventory Service listening on 127.0.0.1:50051 (delay=5.0s, pid=39596)
```

The delay applies only to `CheckInventory`. The `HealthCheck` RPC still answers immediately.

### The client command and the clean DEADLINE_EXCEEDED result

In **Terminal 2**:

> 📸 **Screenshot 6:** Terminal 1 showing `delay=5.0s`, plus this command and output showing `DEADLINE_EXCEEDED` and about 2.0 s.

```text
$ time python grpc_service/order_client.py --item-id item-1 --quantity 1
[order-grpc pid=39598] CheckInventory(item_id='item-1', quantity=1) deadline=2.0s
{
  "status": "failed",
  "order_id": "ord-ded41e39",
  "grpc_status": "DEADLINE_EXCEEDED",
  "message": "Deadline Exceeded",
  "elapsed_seconds": 2.003
}
  0.12s user 0.03s system 7% cpu 2.155 total
```

gRPC Inventory Service log (Terminal 1):

> 📸 **Screenshot 7:** the `abandoning request` line.

```text
2026-10-11 14:38:57,102 [inventory-grpc] INFO Delay mode: sleeping 5.0s before answering
2026-10-11 14:39:02,107 [inventory-grpc] WARNING Client deadline already expired for item_id='item-1'; abandoning request
```

The server is still alive in slow mode, because `HealthCheck` is never delayed:

```text
$ python grpc_service/order_client.py --health
[order-grpc pid=39603] Inventory HealthCheck:
{
  "status": "SERVING",
  "service": "inventory-grpc",
  "pid": 39596,
  "uptime_seconds": 3.7,
  "delay_seconds": 5.0
}
```

**Why this counts as clean handling:**
- The call ended after **2.003 s**, the deadline, not the 5 s delay.
- The client printed a structured result instead of a traceback.
- **`DEADLINE_EXCEEDED`** is the standard gRPC status for "the operation did not finish before its deadline", and the gRPC library produces it automatically on the client side.
- The deadline travels **with** the request, so the server could see that the client had given up (`context.is_active()` returned `False`) and abandon the request instead of sending a response no one would read. Compare the REST Inventory log in section 9, which shows the work completing anyway.

---

## 11. Evidence that the Order Service keeps running

Each health check reports the **process ID (`pid`)**. If a process crashed and was restarted, it would get a new `pid`. In both tests below, the Order side keeps the **same pid** before the failure, after the failure, and while it handles the next successful request. That shows it never crashed.

### REST: a successful request after the timeout

This continues the session from [section 9](#9-rest-timeout-evidence). The Order Service started at the top of [section 4](#4-running-the-rest-version) with **`pid` 39516** and was never restarted.

**Step 1.** Right after the `504`, the Order Service still answers its health check with the same pid:

```text
$ curl -s http://127.0.0.1:5050/health
{"inventory_timeout_seconds":2.0,"inventory_url":"http://127.0.0.1:5001","pid":39516,"service":"order-rest","status":"ok","uptime_seconds":2.4}
```

**Step 2.** In Terminal 1, stop the slow Inventory Service with `Ctrl+C` and start it again in normal mode with `python rest/inventory_service.py`. **Do not touch Terminal 2.**

**Step 3.** Send the same order again to the same Order Service:

> 📸 **Screenshot 8:** both commands below, showing `200 OK` and `"pid":39516`, the same pid as before the timeout.

```text
$ curl -si -X POST http://127.0.0.1:5050/orders -H 'Content-Type: application/json' -d '{"item_id": "item-1", "quantity": 1}'
HTTP/1.1 200 OK
Server: Werkzeug/3.1.9 Python/3.12.13
Date: Sun, 11 Oct 2026 21:38:44 GMT
Content-Type: application/json
Content-Length: 223
Connection: close

{"inventory":{"available":true,"available_quantity":10,"item_id":"item-1","message":"1 unit(s) of 'item-1' available.","requested_quantity":1},"item_id":"item-1","order_id":"ord-391fded0","quantity":1,"status":"confirmed"}

$ curl -s http://127.0.0.1:5050/health
{"inventory_timeout_seconds":2.0,"inventory_url":"http://127.0.0.1:5001","pid":39516,"service":"order-rest","status":"ok","uptime_seconds":6.2}
```

The Order Service log (Terminal 2) shows one uninterrupted process handling the success, the timeout and the next success:

```text
2026-10-11 14:38:38,207 [order-rest] INFO Starting REST Order Service on http://127.0.0.1:5050 -> inventory http://127.0.0.1:5001 (timeout=2.0s, pid=39516)
2026-10-11 14:38:38,278 [order-rest] INFO Order ord-ad2f3b0a confirmed: 2 x item-1
...
2026-10-11 14:38:40,563 [order-rest] WARNING Order ord-9c19c89a: Inventory timed out after 2.00s
2026-10-11 14:38:40,564 [order-rest] INFO 127.0.0.1 - - [11/Oct/2026 14:38:40] "POST /orders HTTP/1.1" 504 -
2026-10-11 14:38:44,353 [order-rest] INFO Order ord-391fded0 confirmed: 1 x item-1
2026-10-11 14:38:44,353 [order-rest] INFO 127.0.0.1 - - [11/Oct/2026 14:38:44] "POST /orders HTTP/1.1" 200 -
```

**Conclusion:** the REST Order Service **did not crash**. It turned the timeout into a `504` response, stayed up (same `pid` 39516), and confirmed the next order as soon as the Inventory Service was healthy again.

### gRPC: a successful request after the deadline failure

Each single-shot `order_client.py --item-id ...` command starts a new process, so it cannot show "still running". To show that, run the Order client as **one long-lived process** in `--interactive` mode and restart the Inventory server underneath it.

1. **Terminal 1:** `python grpc_service/inventory_server.py` (normal mode).
2. **Terminal 2:** `python grpc_service/order_client.py --interactive`. Then type the lines shown below, restarting the server in Terminal 1 where the `── Terminal 1 ──` notes indicate.

> 📸 **Screenshot 9:** this entire Terminal 2 session, showing `OK`, then `DEADLINE_EXCEEDED`, then `OK`, all with `pid=39648`.

```text
$ python grpc_service/order_client.py --interactive
gRPC Order client (pid=39648) -> 127.0.0.1:50051, deadline=2.0s
Enter '<item_id> <quantity>', 'health', or 'quit'.
item-1 2
[order-grpc pid=39648] CheckInventory(item_id='item-1', quantity=2) deadline=2.0s
{
  "status": "confirmed",
  "order_id": "ord-13f4571b",
  "grpc_status": "OK",
  "item_id": "item-1",
  "quantity": 2,
  "inventory": {
    "available": true,
    "available_quantity": 10,
    "message": "2 unit(s) of 'item-1' available."
  },
  "elapsed_seconds": 0.002
}
          ── Terminal 1: Ctrl+C, then: python grpc_service/inventory_server.py --delay 5 ──
item-1 1
[order-grpc pid=39648] CheckInventory(item_id='item-1', quantity=1) deadline=2.0s
{
  "status": "failed",
  "order_id": "ord-c7372fa2",
  "grpc_status": "DEADLINE_EXCEEDED",
  "message": "Deadline Exceeded",
  "elapsed_seconds": 2.009
}
health
[order-grpc pid=39648] Order client alive; Inventory HealthCheck:
{
  "status": "SERVING",
  "service": "inventory-grpc",
  "pid": 39649,
  "uptime_seconds": 3.1,
  "delay_seconds": 5.0
}
          ── Terminal 1: Ctrl+C, then: python grpc_service/inventory_server.py ──
item-1 1
[order-grpc pid=39648] CheckInventory(item_id='item-1', quantity=1) deadline=2.0s
{
  "status": "confirmed",
  "order_id": "ord-87004b69",
  "grpc_status": "OK",
  "item_id": "item-1",
  "quantity": 1,
  "inventory": {
    "available": true,
    "available_quantity": 10,
    "message": "1 unit(s) of 'item-1' available."
  },
  "elapsed_seconds": 0.005
}
health
[order-grpc pid=39648] Order client alive; Inventory HealthCheck:
{
  "status": "SERVING",
  "service": "inventory-grpc",
  "pid": 39675,
  "uptime_seconds": 2.1,
  "delay_seconds": 0.0
}
quit
gRPC Order client (pid=39648) exiting normally.
```

(The lines `item-1 2`, `item-1 1`, `health` and `quit` are what was typed. The `── Terminal 1 ──` lines are annotations, not program output.)

**Conclusion:** the gRPC Order client **did not crash**. One process (`pid=39648`) confirmed an order, received `DEADLINE_EXCEEDED`, stayed responsive (`health` → `SERVING`), confirmed the next order once the server was back in normal mode, and finally exited normally with exit code `0` when told to `quit`. The *server* pid changes (39649 → 39675) only because we deliberately restarted it.

> **Tip:** if you type an order within about a second of restarting the server, you may briefly see `UNAVAILABLE`. That's the client's connection (channel) reconnecting, not a crash. Wait a moment, type `health`, and try again. The client caps the reconnect wait at 1 s. The automated test run in [`docs/test_run_output.txt`](docs/test_run_output.txt) shows this happening and recovering.

---

## 12. REST vs gRPC comparison

**What differences did you observe between implementing the same service contract using REST and gRPC?**

In REST, the contract was only an agreement about URL paths and JSON field names. Outcomes were signalled through HTTP status codes (200, 400, 404, 409, 504), and I had to validate types by hand because JSON happily delivers `"quantity": "two"`. In gRPC, `inventory.proto` was a strongly typed contract from which the client stub and server base class were generated, so a wrong field type could not even be sent, although range checks were still needed because proto3 delivers an omitted `quantity` as `0`. REST was much simpler to set up and test: two Flask files plus plain `curl` were enough, whereas gRPC required a code-generation step, generated files tied to matching `grpcio`/`protobuf` versions, and a dedicated Python client just to make a call. Error handling differed in format: a failed REST call returned a JSON error body alongside the status code, which the Order Service had to translate case by case, while a failed gRPC call returned no message at all, only a standard status code (`NOT_FOUND`, `RESOURCE_EXHAUSTED`, `INVALID_ARGUMENT`) and a details string, raised as a single `RpcError` type. Timeouts also behaved differently: the REST timeout existed only inside the Order Service, so the slow Inventory Service kept working and logged a `200` three seconds after the caller had already returned `504`, whereas the gRPC deadline travelled with the request, which let the server detect that the client had received `DEADLINE_EXCEEDED` and abandon the work.

---

## 13. Automated test script

[`tests/run_all_tests.py`](tests/run_all_tests.py) automates everything above. It starts the real services as subprocesses, restarts the Inventory services in delay mode (and stops them entirely to simulate an outage), and verifies **20 checks**, including that the Order side keeps the same PID throughout.

```bash
# Stop any services you started manually first: the script needs ports 5001, 5050 and 50051.
python tests/run_all_tests.py
```

It takes about 10 seconds and ends with:

> 📸 **Screenshot 10:** the summary line.

```text
======================================================================
SUMMARY: 20/20 checks passed
Service logs: <project-root>/tests/logs
```

The complete output of a real run is committed at [`docs/test_run_output.txt`](docs/test_run_output.txt). The services' own logs from the run are written to `tests/logs/`, which is git-ignored.

---

## 14. Testing checklist

Each assignment requirement, where this README shows the evidence, and the matching automated check in `tests/run_all_tests.py`:

| # | Assignment requirement | Evidence in this README | Automated check |
|---|---|---|---|
| 1 | Order Service accepts `item_id` + `quantity` | §1, §6, §7 | REST successful order -> 200; gRPC successful order -> OK |
| 2 | Order Service has one REST endpoint and one gRPC client flow | §4 (`POST /orders`), §5 (`order_client.py`) | all REST and gRPC checks |
| 3 | Inventory Service has an in-memory inventory | §1 (`item-1`: 10, `item-2`: 5, `item-3`: 0) | n/a |
| 4 | Inventory checks item and quantity availability | §6, §7, §8 | the 404/409 and NOT_FOUND/RESOURCE_EXHAUSTED checks |
| 5 | REST with Flask, JSON request and response bodies | §4, §6 | REST successful order -> 200 |
| 6 | `.proto` with an `InventoryService.CheckInventory` RPC | §5 | gRPC successful order -> OK |
| 7 | Generated Python gRPC files | §2, §3 step 3, §5 step 1 | n/a (the files are imported by every gRPC check) |
| 8 | REST **200** when available | §6 | REST successful order -> 200 |
| 9 | REST **400** for invalid input | §8 | REST invalid quantity -> 400; REST missing item_id -> 400 |
| 10 | REST **404** for unknown item | §8 | REST unknown item -> 404 |
| 11 | REST **409** for insufficient inventory | §8 | REST insufficient quantity -> 409 |
| 12 | REST **504** when the Order Service times out | §9 | REST timeout -> 504 |
| 13 | REST **500** for unexpected errors | §1 outcome table; implemented in the `@app.errorhandler(Exception)` handlers | not triggered by the tests (no natural way to cause it) |
| 14 | Timeout configured on the Order → Inventory HTTP call | §9 "The configured REST timeout" | REST timeout returned promptly (~timeout, not delay) |
| 15 | REST delay option longer than the timeout | §9 (`--delay 5` > 2.0 s) | REST timeout -> 504 |
| 16 | gRPC deadline configured on the Order → Inventory call | §10 "The configured gRPC deadline" | gRPC deadline enforced (~deadline, not delay) |
| 17 | gRPC **INVALID_ARGUMENT** | §8 | gRPC invalid quantity -> INVALID_ARGUMENT; gRPC invalid item_id -> INVALID_ARGUMENT |
| 18 | gRPC **NOT_FOUND** | §8 | gRPC unknown item -> NOT_FOUND |
| 19 | gRPC **RESOURCE_EXHAUSTED** | §8 | gRPC insufficient quantity -> RESOURCE_EXHAUSTED |
| 20 | gRPC **DEADLINE_EXCEEDED** | §10 | gRPC deadline -> DEADLINE_EXCEEDED |
| 21 | gRPC delay option longer than the deadline | §10 (`--delay 5` > 2.0 s) | gRPC deadline -> DEADLINE_EXCEEDED |
| 22 | Delay mode via environment variable or command-line option | §9, §10 (`--delay` and `INVENTORY_DELAY_SECONDS`) | the test uses `--delay` |
| 23 | REST returns a clean timeout response | §9 | REST timeout -> 504 |
| 24 | gRPC returns a clean deadline-exceeded response | §10 | gRPC deadline -> DEADLINE_EXCEEDED |
| 25 | Order Service stays running after the REST failure | §11 REST (same pid 39516, then 200) | REST Order Service healthy after timeout (same PID); REST Order Service serves orders again after failures (same PID) |
| 26 | Order Service stays running after the gRPC failure | §11 gRPC (same pid 39648, then OK) | gRPC Order client alive after deadline; gRPC Order client serves orders again after failures (same PID) |
| 27 | Health endpoints / commands prove the services are up | §4 `GET /health`, §5 `--health`, §10, §11 | the health checks above |
| 28 | Order Service handles connection errors cleanly | §1 outcome table (503 / UNAVAILABLE) | REST Inventory down -> 503; gRPC Inventory down -> UNAVAILABLE |
| 29 | 4–6 sentence REST vs gRPC comparison | §12 (6 sentences) | n/a |

---

## 15. Screenshot guide

If your submission needs screenshots, capture the blocks marked 📸 in this README:

| # | Section | What must be visible |
|---|---|---|
| 1 | §6 | `curl -si ... item-1, quantity 2` → `HTTP/1.1 200 OK` and the `"status":"confirmed"` body |
| 2 | §7 | `order_client.py --item-id item-1 --quantity 2` → `"grpc_status": "OK"` |
| 3 | §8 | The REST `404`, `409` and `400` responses |
| 4 | §8 | The gRPC `NOT_FOUND`, `RESOURCE_EXHAUSTED` and `INVALID_ARGUMENT` outputs |
| 5 | §9 | Terminal 1 with `delay=5.0s` **and** Terminal 3 with `HTTP/1.1 504 GATEWAY TIMEOUT` and `2.0… total` |
| 6 | §10 | Terminal 1 with `delay=5.0s` **and** Terminal 2 with `"grpc_status": "DEADLINE_EXCEEDED"` and `elapsed_seconds` ≈ 2.0 |
| 7 | §10 | The server log line `Client deadline already expired ... abandoning request` |
| 8 | §11 | REST: `200 OK` after the timeout, plus `/health` with the **same pid** as before |
| 9 | §11 | gRPC `--interactive` session: `OK` → `DEADLINE_EXCEEDED` → `OK`, all with the same `pid` |
| 10 | §13 | `SUMMARY: 20/20 checks passed` |

Tip: arrange the terminals side by side so a single screenshot shows both the delayed Inventory Service and the Order side's clean failure.
