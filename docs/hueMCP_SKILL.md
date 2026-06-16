# Skill: Philips Hue (hueMCP)

> Manage Philips Hue rooms, lights, and scenes on the local network through the
> `hueMCP` server.

This skill is the operating guide. The authoritative tool contract is `hueMCP_API_SPEC.md`.

---

## Role

You control Philips Hue lighting via the `hueMCP` server: list and inspect rooms,
lights, and scenes; turn lights or whole rooms on/off; set brightness, color, and
white color temperature; and activate scenes.

**This controls real lights in someone's home.** Report what actually happened. Read
tools (`list_*`, `get_*`, `status`) never change anything — use them freely.

### What this server does NOT do
- **No bridge pairing.** Creating the bridge credential is a one-time local setup step
  (`huemcp-cli pair`), not an agent action. If `status` shows `paired: false`, tell the
  user to run it; do not try to work around it.
- **No scene authoring** beyond activating existing scenes.
- **No entertainment / streaming / sync** modes.

---

## How to connect
- **Endpoint:** `POST https://<host>:8910/mcp` — MCP / JSON-RPC 2.0 over HTTPS.
- **TLS:** self-signed. Pin the cert from `GET https://<host>:8910/cert`.
- **Auth (every request):** `Authorization: Bearer ${HUE_AUTH_TOKEN}`.

```yaml
mcp_servers:
  hue:
    type: http
    url: "https://<host>:8910/mcp"
    headers:
      Authorization: "Bearer ${HUE_AUTH_TOKEN}"
```

---

## Tools (9)

### Status
| Tool | Args | Notes |
|---|---|---|
| `status` | — | `paired`, `reachable`, and light/room/scene counts |

### Lights
| Tool | Args | Notes |
|---|---|---|
| `list_lights` | — | id, name, on, brightness |
| `get_light` | `light_id` | full state incl. color + color temperature |
| `set_light` | `light_id`, + at least one of `on`, `brightness` (0-100), `color_xy` ({x,y}), `color_temperature_mirek` (153-500) | mirek: 153=cool, 500=warm |

### Rooms
| Tool | Args | Notes |
|---|---|---|
| `list_rooms` | — | id, name, on, brightness, grouped_light_id |
| `get_room` | `room_id` | room detail + the lights it contains |
| `set_room` | `room_id`, + at least one of `on`, `brightness` (0-100) | sets the whole room at once |

### Scenes
| Tool | Args | Notes |
|---|---|---|
| `list_scenes` | — | id, name, room_id, room_name |
| `activate_scene` | `scene_id` | recalls the scene into its room |

---

## Operating rules
1. **Resolve ids first.** Names are not ids. To act on "the living room", call
   `list_rooms`, find the room whose `name` matches, then use its `id`. Same for lights
   and scenes (`list_lights` / `list_scenes`).
2. **Prefer `set_room` for "the whole room"** and `set_light` for a single bulb. To
   "turn the lights to 40%", `set_room(room_id, brightness=40)` — it also implies on.
3. **Branch on the error `kind`, not the message.** `not_found` → re-list and pick a
   valid id. `hue_unreachable` → the bridge is unconfigured or offline; report it and
   stop (don't retry-loop). `unsupported` → report the gap. `invalid_argument` → fix
   the call (e.g. you passed `set_light`/`set_room` with no field to change).
4. **Brightness is 0-100.** Color is CIE xy (`{"x":..,"y":..}`, each 0-1). White
   temperature is mirek 153-500 (lower = cooler/bluer, higher = warmer/amber).
5. **Activating a scene** sets its room's lights to the scene; it overrides current
   per-light state in that room.

---

## Common tasks

**"Dim the living room to 30%":**
```
1. list_rooms -> find {name: "Living Room"} -> room_id
2. set_room(room_id, brightness=30)
```

**"Turn off the lamp":**
```
1. list_lights -> find {name: "Lamp"} -> light_id
2. set_light(light_id, on=false)
```

**"Set movie scene":**
```
1. list_scenes -> find {name: "Movie"} -> scene_id
2. activate_scene(scene_id)
```

---

## Troubleshooting
- **Everything returns `hue_unreachable`:** the bridge isn't paired or isn't reachable.
  Check `status`; if `paired: false`, the user must run `huemcp-cli pair`. If paired but
  unreachable, the bridge may be off or its IP changed (re-pair).
- **A name doesn't resolve:** list the resource and match on `name` — there may be
  duplicates or a slightly different name.

## Pitfalls
- `set_light` / `set_room` with no state field returns `invalid_argument` — always pass
  at least one of on/brightness/color.
- `room.brightness` is the grouped-light average; individual lights may differ. Use
  `get_room` to see per-light state.
