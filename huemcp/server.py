"""JSON-RPC 2.0 / MCP dispatch and the Starlette HTTPS app.

`dispatch()` is a pure function (no HTTP, no auth) so it can be unit-tested directly.
The Starlette layer adds bearer auth, body-size limits, and the /cert and /healthz routes.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from contextlib import nullcontext

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import MCP_PROTOCOL_VERSION, SERVER_NAME, __version__
from .client import HueBackend
from .errors import HueMCPError
from .tools import REGISTRY, list_tools

MAX_BODY = 8192

log = logging.getLogger("huemcp.server")


# --- JSON-RPC helpers --------------------------------------------------------

def _rpc_error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _rpc_result(id_, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _tool_error_envelope(err: HueMCPError) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(err.to_dict())}], "isError": True}


def _call_tool(backend: HueBackend, params: dict, write_lock=None) -> dict:
    name = params.get("name")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return _tool_error_envelope(HueMCPError("invalid_argument", "arguments must be an object"))
    tool = REGISTRY.get(name)
    if tool is None:
        log.warning("call to unknown tool %r", name)
        return _tool_error_envelope(HueMCPError("invalid_argument", f"unknown tool: {name}"))

    serialize = tool["mutates"] and write_lock is not None
    lock_cm = write_lock if serialize else nullcontext()
    t0 = time.perf_counter()
    try:
        with lock_cm:
            result = tool["handler"](backend, args)
    except HueMCPError as exc:
        log.warning("tool %s -> %s in %.0fms: %s",
                    name, exc.kind, (time.perf_counter() - t0) * 1000, exc.message)
        return _tool_error_envelope(exc)
    except Exception as exc:  # noqa: BLE001 - never leak a raw traceback to the client
        log.exception("tool %s crashed after %.0fms", name, (time.perf_counter() - t0) * 1000)
        return _tool_error_envelope(HueMCPError("upstream_error", str(exc)))
    log.info("tool %s ok in %.0fms", name, (time.perf_counter() - t0) * 1000)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


def dispatch(payload, backend: HueBackend, write_lock=None):
    """Handle one JSON-RPC payload. Returns a response dict, or None for notifications."""
    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, "invalid request")
    method = payload.get("method")
    id_ = payload.get("id")
    if not isinstance(method, str):
        return _rpc_error(id_, -32600, "invalid request: missing method")
    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        return _rpc_result(id_, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
        })
    if method == "tools/list":
        return _rpc_result(id_, {"tools": list_tools()})
    if method == "tools/call":
        params = payload.get("params")
        if not isinstance(params, dict) or "name" not in params:
            return _rpc_error(id_, -32602, "invalid params: missing tool name")
        return _rpc_result(id_, _call_tool(backend, params, write_lock))
    return _rpc_error(id_, -32601, f"method not found: {method}")


# --- Starlette app -----------------------------------------------------------

def create_app(backend: HueBackend, auth_token: str, cert_pem: str | None = None) -> Starlette:
    # Hue bridge calls are blocking (requests). dispatch runs in a worker thread so one
    # slow call never stalls the event loop. Mutating tools serialize on this lock;
    # reads run fully concurrently.
    write_lock = threading.Lock()

    async def mcp_endpoint(request: Request) -> Response:
        auth = request.headers.get("authorization", "")
        presented = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        # Constant-time compare so a wrong token can't be recovered byte-by-byte via timing.
        if not hmac.compare_digest(presented, auth_token):
            log.warning("unauthorized request from %s", request.client.host if request.client else "?")
            return JSONResponse(_rpc_error(None, -32001, "unauthorized"), status_code=401)

        body = await request.body()
        if not body or len(body) > MAX_BODY:
            return JSONResponse(_rpc_error(None, -32600, "empty or oversized request"), status_code=400)
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return JSONResponse(_rpc_error(None, -32700, "parse error"), status_code=400)

        response = await run_in_threadpool(dispatch, payload, backend, write_lock)
        if response is None:
            return Response(status_code=202)
        return JSONResponse(response)

    async def cert_endpoint(request: Request) -> Response:
        if not cert_pem:
            return PlainTextResponse("no certificate available", status_code=404)
        return PlainTextResponse(cert_pem, media_type="application/x-pem-file")

    async def health_endpoint(request: Request) -> Response:
        # Cheap, non-blocking: reports config/pairing state only (no bridge call), so a
        # down/unreachable bridge can never hang the health check. `paired=false` ⇒ warn.
        paired = backend.paired
        return JSONResponse({
            "status": "ok" if paired else "warn",
            "server": SERVER_NAME,
            "version": __version__,
            "paired": paired,
            "bridge_ip": backend.bridge_ip,
        })

    return Starlette(routes=[
        Route("/mcp", mcp_endpoint, methods=["POST"]),
        Route("/cert", cert_endpoint, methods=["GET"]),
        Route("/healthz", health_endpoint, methods=["GET"]),
    ])
