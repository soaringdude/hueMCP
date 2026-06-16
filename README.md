# hueMCP

An MCP server for **Philips Hue**: manage rooms, lights, and scenes over the local
network via the Hue **CLIP v2 API** (wrapping the [`python-hue-v2`](https://github.com/FengChendian/python-hue-v2)
library). Built in the house MCP style (see `~/.claude/skills/new-mcp`).

## Documentation
- `docs/hueMCP_API_SPEC.md` — authoritative wire contract (tools, args, error kinds).
- `docs/hueMCP_SKILL.md` — operating guide for a consuming agent.
- `CLAUDE.md` — maintainer runbook (architecture, gotchas, deploy).

## Tools (9)
- **Lights:** `list_lights`, `get_light`, `set_light`
- **Rooms:** `list_rooms`, `get_room`, `set_room`
- **Scenes:** `list_scenes`, `activate_scene`
- **Status:** `status`

## Setup

```bash
uv sync --extra dev
```

### 1. Pair with the bridge (one time)
The bridge needs an application key. Press the bridge's link button, then:

```bash
uv run huemcp-cli discover            # find the bridge IP (mDNS)
uv run huemcp-cli pair                # auto-discover + press link button + store the key
# or: uv run huemcp-cli pair --ip 192.0.2.5
```

This writes `~/.hueMCP/state.json` (`bridge_ip` + `app_key`, mode 0600). Alternatively
set `HUE_BRIDGE_IP` and `HUE_APP_KEY` directly (e.g. in the plist) and skip pairing.

### 2. Run the server
```bash
HUE_AUTH_TOKEN=$(openssl rand -hex 32) uv run huemcp
```
Serves MCP / JSON-RPC 2.0 over HTTPS on `:8910`. Pin the self-signed cert:
```bash
curl -k https://localhost:8910/cert > huemcp.pem
```

### 3. Exercise it
```bash
HUE_AUTH_TOKEN=<token> uv run huemcp-cli rooms
HUE_AUTH_TOKEN=<token> uv run huemcp-cli call set_room room_id=<id> on=true brightness=40
HUE_AUTH_TOKEN=<token> uv run huemcp-cli call activate_scene scene_id=<id>
```

## Environment variables
| Var | Purpose | Default |
|---|---|---|
| `HUE_AUTH_TOKEN` | bearer token for MCP clients (required) | — |
| `HUE_BRIDGE_IP` | bridge IP (else read from `state.json`) | — |
| `HUE_APP_KEY` | bridge app key (else read from `state.json`) | — |
| `HUE_PORT` | HTTPS port | `8910` |
| `HUE_BIND` | bind address | `0.0.0.0` |
| `HUE_TLS` | `selfsigned` or `none` | `selfsigned` |
| `HUE_STATE_DIR` | cert/key + pairing state | `~/.hueMCP` |
| `HUE_LOG_LEVEL` | log level | `info` |
| `HUE_CACHE_TTL_S` | TTL for cached write-path lookups (room→grouped_light, light ids) | `600` |

## Tests
```bash
uv run pytest          # no bridge required (fake bridge)
```

## MCP client config
```json
{
  "mcpServers": {
    "hue": {
      "type": "http",
      "url": "https://localhost:8910/mcp",
      "headers": { "Authorization": "Bearer <HUE_AUTH_TOKEN>" }
    }
  }
}
```

## Running as a service
launchd LaunchDaemon (`com.hueMCP.plist`) + the shared `mcp` harness:
```bash
mcp hue health    # paired? reachable? bridge IP
mcp hue restart
mcp hue logs -f
```
See `CLAUDE.md` for install/reload steps.
