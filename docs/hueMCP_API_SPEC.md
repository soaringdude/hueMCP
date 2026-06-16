# hueMCP API Spec

**Version:** v1.0.0
**Transport:** MCP / JSON-RPC 2.0 over HTTPS, `POST /mcp`
**Auth:** `Authorization: Bearer ${HUE_AUTH_TOKEN}` on every request (401 otherwise)
**TLS:** self-signed; fetch the pinning cert at `GET /cert`
**Upstream:** Philips Hue CLIP v2 API via `python-hue-v2`

Source of truth for the wire contract. Keep `hueMCP_SKILL.md` in sync.

## JSON-RPC envelope
Request: `{"jsonrpc":"2.0","id":<n>,"method":<m>,"params":{...}}`
Result: `{"jsonrpc":"2.0","id":<n>,"result":{...}}`
Error: `{"jsonrpc":"2.0","id":<n>,"error":{"code":<c>,"message":<s>}}`

Methods: `initialize`, `tools/list`, `tools/call`. Notifications → HTTP 202.
Protocol codes: -32700 parse, -32600 invalid request, -32601 method not found,
-32602 invalid params, -32001 unauthorized.

## Tool results & errors
`tools/call` → `{"content":[{"type":"text","text":"<json>"}]}`. On a tool-level failure:
`{"content":[...],"isError":true}` whose text is:
```json
{ "kind": "<error-kind>", "error": "<message>", "hint": "<optional next action>" }
```

### Error kinds
| kind | meaning |
|---|---|
| `invalid_argument` | bad / missing / contradictory args (e.g. set_* with no field) |
| `not_found` | light / room / scene id does not exist |
| `unsupported` | resource can't do this (e.g. room with no grouped_light) |
| `hue_unreachable` | bridge not configured (unpaired) or unreachable on the LAN |
| `upstream_error` | unexpected failure surfaced verbatim |

Clients MUST treat an unrecognized `kind` as `upstream_error` (so adding a kind is a
backward-compatible change).

## Tools

### `status` — read-only
- **Args:** none
- **Returns:** `{ "paired": bool, "bridge_ip": str|null, "reachable": bool, "lights": int, "rooms": int, "scenes": int }`. `reachable` and the counts are present only when paired; if paired but the bridge is unreachable, `reachable:false` and an `"error": str` replaces the counts.
- **Errors:** none (reports state)

### `list_lights` — read-only
- **Returns:** `[{ "id", "name", "on": bool, "brightness": 0-100 }]`
- **Errors:** `hue_unreachable`

### `get_light` — read-only
- **Args:** `light_id` (string, required)
- **Returns:** `{ "id","name","on","brightness","color_temperature_mirek","color_xy","owner" }`
- **Errors:** `not_found`, `hue_unreachable`

### `set_light` — mutating
- **Args:** `light_id` (required); at least one of `on` (bool), `brightness` (0-100),
  `color_xy` (`{"x":0-1,"y":0-1}`), `color_temperature_mirek` (153-500)
- **Returns:** `{ "success": true, "id": light_id }`
- **Errors:** `invalid_argument` (no field, or a value out of range: `brightness` ∉ 0-100, `color_temperature_mirek` ∉ 153-500, malformed `color_xy`), `not_found`, `hue_unreachable`

### `list_rooms` — read-only
- **Returns:** `[{ "id","name","grouped_light_id","device_count","on","brightness" }]`
- **Errors:** `hue_unreachable`

### `get_room` — read-only
- **Args:** `room_id` (required)
- **Returns:** `{ "id","name","grouped_light_id","on","brightness","lights":[{id,name,on,brightness}] }`
- **Errors:** `not_found`, `hue_unreachable`

### `set_room` — mutating
- **Args:** `room_id` (required); at least one of `on` (bool), `brightness` (0-100)
- **Returns:** `{ "success": true, "room_id", "grouped_light_id" }`
- **Errors:** `invalid_argument` (no field, or `brightness` ∉ 0-100), `not_found`, `unsupported`, `hue_unreachable`

### `list_scenes` — read-only
- **Returns:** `[{ "id","name","room_id","room_name" }]`
- **Errors:** `hue_unreachable`

### `activate_scene` — mutating
- **Args:** `scene_id` (required)
- **Returns:** `{ "success": true, "scene_id" }`
- **Errors:** `not_found`, `hue_unreachable`

## Health
`GET /healthz` (unauthenticated) → `{ "status": "ok"|"warn", "server": "hueMCP",
"version": "<v>", "paired": bool, "bridge_ip": str|null }`. `paired:false` ⇒ `warn`.
No bridge call is made (cannot hang on a down bridge). HTTP 200.

## Units
- `brightness`: 0-100 percent.
- `color_xy`: CIE xy chromaticity, `{"x","y"}` each 0.0-1.0.
- `color_temperature_mirek`: 153 (cool) – 500 (warm).

## Versioning
Per `~/.claude/skills/new-mcp/references/versioning.md`: new tools / optional args /
result fields / error kinds are additive (minor); removals, renames, type or default
changes are breaking (major). Update this spec and the SKILL together.
