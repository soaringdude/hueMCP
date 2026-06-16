"""huemcp-cli: a client for a running hueMCP server, plus bridge discovery + pairing.

The HTTP client portion is stdlib-only (urllib + ssl). The `discover` and `pair`
setup commands lazy-import python_hue_v2 + the backend helpers — pairing mints a
bridge credential, so it is a local management step, never an MCP tool.

Examples:
    huemcp-cli pair                 # discover a bridge, press its link button, store the key
    huemcp-cli pair --ip 192.0.2.5
    huemcp-cli discover
    huemcp-cli list-tools
    huemcp-cli rooms
    huemcp-cli call set_room room_id=<id> on=true brightness=40
    huemcp-cli call activate_scene scene_id=<id>
    huemcp-cli health

Connection from flags or env: HUE_URL (default https://localhost:8910),
HUE_AUTH_TOKEN, HUE_CAFILE. Pairing state dir: HUE_STATE_DIR (default ~/.hueMCP).
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://localhost:8910"
_NO_SERVER = ("pair", "discover")  # commands that don't talk to the running server


# --- argument parsing helpers (pure, unit-tested) ----------------------------

def coerce_value(raw: str):
    low = raw.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low == "null":
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if raw[:1] in "[{":
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return raw


def parse_kv_args(pairs: list[str]) -> dict:
    """Parse ['k=v', 'n=3', 'flag=true', 'obj:={...}'] into a dict.
    Use `key:=<json>` (httpie style) to force a raw-JSON value."""
    args: dict = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"expected key=value, got: {pair!r}")
        key, _, value = pair.partition("=")
        if key.endswith(":"):
            args[key[:-1]] = json.loads(value)
        else:
            args[key] = coerce_value(value)
    return args


def render_response(resp: dict) -> tuple[str, bool]:
    if "error" in resp:
        err = resp["error"]
        return f"! RPC error {err['code']}: {err['message']}", False
    result = resp.get("result", {})
    if isinstance(result, dict) and "content" in result:
        text = result["content"][0].get("text", "")
        try:
            data = json.loads(text)
        except ValueError:
            return text, not result.get("isError", False)
        if result.get("isError"):
            hint = f"\n  hint: {data['hint']}" if data.get("hint") else ""
            return f"! [{data.get('kind')}] {data.get('error')}{hint}", False
        return json.dumps(data, indent=2), True
    return json.dumps(result, indent=2), True


# --- HTTP client -------------------------------------------------------------

class Client:
    def __init__(self, base_url: str, token: str, cafile: str | None = None, insecure: bool = False):
        self.base = base_url.rstrip("/")
        self.token = token
        self._id = 0
        self.ctx = self._make_ctx(cafile, insecure)

    def _make_ctx(self, cafile, insecure):
        if not self.base.startswith("https://"):
            return None
        if cafile:
            return ssl.create_default_context(cafile=os.path.expanduser(cafile))
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        req = urllib.request.Request(
            self.base + "/mcp",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return json.loads(body)
            except ValueError:
                return {"error": {"code": e.code, "message": e.reason}}

    def get(self, path: str) -> str:
        req = urllib.request.Request(self.base + path)
        with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
            return r.read().decode()


# --- command handlers (server) -----------------------------------------------

def _call_tool(client: Client, name: str, arguments: dict) -> int:
    text, ok = render_response(client.rpc("tools/call", {"name": name, "arguments": arguments}))
    print(text)
    return 0 if ok else 1


def _cmd_list_tools(client: Client, args) -> int:
    resp = client.rpc("tools/list")
    if "error" in resp:
        print(render_response(resp)[0])
        return 1
    tools = resp["result"]["tools"]
    if args.json:
        print(json.dumps(tools, indent=2))
        return 0
    width = max((len(t["name"]) for t in tools), default=0)
    for t in sorted(tools, key=lambda x: x["name"]):
        print(f"  {t['name']:<{width}}  {t['description']}")
    print(f"\n{len(tools)} tools")
    return 0


def _cmd_call(client: Client, args) -> int:
    try:
        arguments = parse_kv_args(args.args)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"! bad arguments: {e}", file=sys.stderr)
        return 2
    return _call_tool(client, args.tool, arguments)


def _cmd_raw(client: Client, args) -> int:
    params = json.loads(args.params) if args.params else None
    resp = client.rpc(args.method, params)
    print(json.dumps(resp, indent=2))
    return 0 if "error" not in resp else 1


def _cmd_cert(client: Client, args) -> int:
    pem = client.get("/cert")
    if args.save:
        path = os.path.expanduser(args.save)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(pem)
        print(f"saved certificate to {path}")
    else:
        print(pem)
    return 0


def _cmd_health(client: Client, args) -> int:
    print(client.get("/healthz"))
    return 0


def _cmd_status(client: Client, args) -> int:
    return _call_tool(client, "status", {})


def _cmd_lights(client: Client, args) -> int:
    return _call_tool(client, "list_lights", {})


def _cmd_rooms(client: Client, args) -> int:
    return _call_tool(client, "list_rooms", {})


def _cmd_scenes(client: Client, args) -> int:
    return _call_tool(client, "list_scenes", {})


# --- command handlers (setup: discovery + pairing) ---------------------------

def _state_path() -> Path:
    return Path(os.environ.get("HUE_STATE_DIR", os.path.expanduser("~/.hueMCP"))) / "state.json"


def _cmd_discover(args) -> int:
    from huemcp.client import discover_bridges
    print("discovering bridges (mDNS)...", file=sys.stderr)
    found = discover_bridges()
    if not found:
        print("no bridges found. Ensure you're on the same LAN; or pass --ip to `pair`.")
        return 1
    for ip in found:
        print(ip)
    return 0


def _cmd_pair(args) -> int:
    from huemcp.client import create_app_key, discover_bridges, save_state

    ip = args.ip
    if not ip:
        print("discovering bridge (mDNS)...", file=sys.stderr)
        found = discover_bridges()
        if not found:
            print("! no bridge found; re-run with --ip <bridge-ip>", file=sys.stderr)
            return 1
        ip = found[0]
        print(f"using bridge at {ip}", file=sys.stderr)

    sp = _state_path()
    print(f"\nPress the round LINK button on the Hue bridge ({ip}), then press Enter here.",
          file=sys.stderr)
    for attempt in range(1, args.attempts + 1):
        try:
            input()
        except EOFError:
            pass
        try:
            key = create_app_key(ip)
        except Exception as exc:  # noqa: BLE001 - bridge rejects until the button is pressed
            print(f"  not paired yet ({exc}). Press the link button and Enter to retry "
                  f"({attempt}/{args.attempts}).", file=sys.stderr)
            continue
        save_state(sp, ip, key)
        print(f"paired. Wrote bridge_ip + app_key to {sp} (0600). Restart the server to use it.")
        return 0
    print("! pairing failed; the link button was not detected.", file=sys.stderr)
    return 1


# --- argument parser ---------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="huemcp-cli", description="Client + pairing for a hueMCP server.")
    p.add_argument("--url", default=os.environ.get("HUE_URL", DEFAULT_URL), help="server base URL (env HUE_URL)")
    p.add_argument("--token", default=os.environ.get("HUE_AUTH_TOKEN", ""), help="bearer token (env HUE_AUTH_TOKEN)")
    p.add_argument("--cafile", default=os.environ.get("HUE_CAFILE"), help="pin TLS to this PEM cert (env HUE_CAFILE)")
    p.add_argument("--insecure", action="store_true", help="skip TLS verification")
    p.add_argument("--secure", action="store_true", help="require TLS verification (do not auto-skip for self-signed)")
    p.add_argument("--json", action="store_true", help="raw JSON output where applicable")

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list-tools", help="list available tools").set_defaults(func=_cmd_list_tools)
    c = sub.add_parser("call", help="call a tool with key=value args")
    c.add_argument("tool")
    c.add_argument("args", nargs="*", help="key=value (use key:=<json> for raw JSON)")
    c.set_defaults(func=_cmd_call)
    r = sub.add_parser("raw", help="send a raw JSON-RPC method")
    r.add_argument("method")
    r.add_argument("params", nargs="?", help="JSON params object")
    r.set_defaults(func=_cmd_raw)
    ce = sub.add_parser("cert", help="fetch the server certificate")
    ce.add_argument("--save", help="write the PEM to this path instead of stdout")
    ce.set_defaults(func=_cmd_cert)
    sub.add_parser("health", help="GET /healthz").set_defaults(func=_cmd_health)

    # friendly read shortcuts
    sub.add_parser("status", help="bridge status").set_defaults(func=_cmd_status)
    sub.add_parser("lights", help="list lights").set_defaults(func=_cmd_lights)
    sub.add_parser("rooms", help="list rooms").set_defaults(func=_cmd_rooms)
    sub.add_parser("scenes", help="list scenes").set_defaults(func=_cmd_scenes)

    # setup (no running server needed)
    sub.add_parser("discover", help="find Hue bridges on the LAN (mDNS)").set_defaults(func=_cmd_discover)
    pr = sub.add_parser("pair", help="press the bridge link button and store an app key")
    pr.add_argument("--ip", help="bridge IP (else auto-discover)")
    pr.add_argument("--attempts", type=int, default=5, help="link-button retries (default 5)")
    pr.set_defaults(func=_cmd_pair)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command in _NO_SERVER:
        return args.func(args)

    insecure = args.insecure
    if args.url.startswith("https://") and not args.cafile and not args.secure and not insecure:
        print("note: skipping TLS verification for self-signed cert (use --cafile to pin, or --secure to enforce)",
              file=sys.stderr)
        insecure = True

    if args.command not in ("cert", "health") and not args.token:
        print("! no token: set --token or HUE_AUTH_TOKEN", file=sys.stderr)
        return 2

    client = Client(args.url, args.token, cafile=args.cafile, insecure=insecure)
    try:
        return args.func(client, args)
    except urllib.error.URLError as e:
        print(f"! connection failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
