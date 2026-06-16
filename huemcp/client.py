"""HueBackend: the ONE place that talks to the Hue bridge (via python-hue-v2).

Reads use the bridge-level getters (one HTTP call per resource type, returning raw
CLIP v2 dicts which we parse) rather than the per-attribute wrapper objects (which
re-fetch on every property access). Writes use set_light / set_grouped_light_service
/ set_scene. The Hue instance is built lazily and injectable so tests need no bridge.

Discovery + pairing helpers (discover_bridges / create_app_key / save_state) are used
by `huemcp-cli pair`; they are setup-time operations, deliberately NOT exposed as MCP
tools (an agent must not be able to mint bridge credentials).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from .errors import HueMCPError

log = logging.getLogger("huemcp.client")


# --- argument validation (the contract: out-of-range/malformed -> invalid_argument) ---
# server.py does not enforce the inputSchema bounds, so the backend is the single point
# that guarantees the documented ranges before anything reaches the bridge.

def _validate_brightness(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise HueMCPError("invalid_argument", "brightness must be a number in 0-100")
    if not (0.0 <= f <= 100.0):
        raise HueMCPError("invalid_argument", f"brightness {f} out of range 0-100")
    return f


def _validate_mirek(v):
    if v is None:
        return None
    try:
        i = int(v)
    except (TypeError, ValueError):
        raise HueMCPError("invalid_argument", "color_temperature_mirek must be an integer in 153-500")
    if not (153 <= i <= 500):
        raise HueMCPError("invalid_argument", f"color_temperature_mirek {i} out of range 153-500")
    return i


def _validate_xy(v):
    if v is None:
        return None
    if not isinstance(v, dict) or "x" not in v or "y" not in v:
        raise HueMCPError("invalid_argument", "color_xy must be an object with x and y")
    try:
        x, y = float(v["x"]), float(v["y"])
    except (TypeError, ValueError):
        raise HueMCPError("invalid_argument", "color_xy x and y must be numbers in 0-1")
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise HueMCPError("invalid_argument", "color_xy x and y must be in 0-1")
    return {"x": x, "y": y}


# --- state persistence (written by `huemcp-cli pair`) ------------------------

def load_state(state_path: Path) -> tuple[str | None, str | None]:
    try:
        d = json.loads(Path(state_path).read_text())
        return d.get("bridge_ip"), d.get("app_key")
    except (OSError, ValueError):
        return None, None


def save_state(state_path: Path, bridge_ip: str, app_key: str) -> None:
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"bridge_ip": bridge_ip, "app_key": app_key}, indent=2)
    # Create 0600 BEFORE writing: the file holds the bridge app key (a secret), so it
    # must never exist world-readable, even briefly. fchmod covers a pre-existing file.
    fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)


# --- discovery + pairing (setup-time; used by the CLI) -----------------------

def discover_bridges(wait_s: float = 2.0) -> list[str]:
    """Return a list of bridge IP addresses found via mDNS. Empty if none."""
    from python_hue_v2 import BridgeFinder
    finder = BridgeFinder()
    time.sleep(wait_s)
    try:
        return list(finder.get_bridge_addresses())
    except Exception as exc:  # noqa: BLE001
        log.warning("bridge discovery failed: %s", exc)
        return []


def create_app_key(bridge_ip: str) -> str:
    """Press the bridge link button first, then call this. Returns the app key.
    Raises if the button has not been pressed (the bridge rejects the request)."""
    from python_hue_v2 import Hue
    hue = Hue(bridge_ip)
    return hue.bridge.connect()


# --- backend -----------------------------------------------------------------

class HueBackend:
    def __init__(self, *, bridge_ip: str | None, app_key: str | None,
                 state_path: Path | None = None, hue_factory=None,
                 cache_ttl_s: float = 600.0, clock=None):
        self._bridge_ip = bridge_ip
        self._app_key = app_key
        self._state_path = state_path
        self._hue_factory = hue_factory  # (ip, key) -> object with a .bridge; injectable for tests
        self._hue = None
        # Cache for STABLE structural lookups used on the write path: room -> grouped_light
        # id, and the set of light ids. State (on/brightness) is never cached. TTL-bounded
        # with refresh-on-miss; opportunistically warmed by list_rooms / list_lights.
        self._cache_ttl_s = cache_ttl_s
        self._clock = clock or time.monotonic
        self._cache_lock = threading.Lock()
        self._room_gl: dict | None = None      # room_id -> grouped_light_id (or None)
        self._room_gl_at = 0.0
        self._light_ids: set | None = None      # known light ids
        self._light_ids_at = 0.0

    @classmethod
    def from_config(cls, config) -> "HueBackend":
        ip, key = config.bridge_ip, config.app_key
        if not (ip and key):
            s_ip, s_key = load_state(config.state_path)
            ip = ip or s_ip
            key = key or s_key
        return cls(bridge_ip=ip, app_key=key, state_path=config.state_path,
                   cache_ttl_s=getattr(config, "cache_ttl_s", 600.0))

    @property
    def paired(self) -> bool:
        return bool(self._bridge_ip and self._app_key)

    @property
    def bridge_ip(self) -> str | None:
        return self._bridge_ip

    def _bridge(self):
        if not self.paired:
            raise HueMCPError(
                "hue_unreachable", "Hue bridge is not configured.",
                hint="Run `huemcp-cli pair` (press the bridge link button) or set "
                     "HUE_BRIDGE_IP and HUE_APP_KEY.",
            )
        if self._hue is None:
            if self._hue_factory is not None:
                self._hue = self._hue_factory(self._bridge_ip, self._app_key)
            else:
                from python_hue_v2 import Hue
                self._hue = Hue(self._bridge_ip, self._app_key)
        return self._hue.bridge

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HueMCPError:
            raise
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if "Connection" in name or "Timeout" in name or "ConnectError" in name:
                raise HueMCPError(
                    "hue_unreachable", f"cannot reach the bridge at {self._bridge_ip}: {exc}",
                    hint="Check the bridge is powered and on the LAN; re-pair if its IP changed.",
                )
            raise HueMCPError("upstream_error", f"{getattr(fn, '__name__', 'bridge call')} failed: {exc}")

    # --- parse helpers (CLIP v2 resource dicts) ---

    @staticmethod
    def _name(resource: dict) -> str | None:
        return (resource.get("metadata") or {}).get("name")

    @staticmethod
    def _grouped_light_id(room: dict) -> str | None:
        for svc in room.get("services", []):
            if svc.get("rtype") == "grouped_light":
                return svc.get("rid")
        return None

    def _light_summary(self, light: dict) -> dict:
        return {
            "id": light.get("id"),
            "name": self._name(light),
            "on": (light.get("on") or {}).get("on"),
            "brightness": (light.get("dimming") or {}).get("brightness"),
        }

    def _light_detail(self, light: dict) -> dict:
        out = self._light_summary(light)
        ct = light.get("color_temperature") or {}
        color = light.get("color") or {}
        out.update({
            "color_temperature_mirek": ct.get("mirek"),
            "color_xy": color.get("xy"),
            "owner": (light.get("owner") or {}).get("rid"),
        })
        return out

    # --- structural cache (room->grouped_light, light ids) -------------------
    # Only stable topology is cached; live state is always fetched fresh. The write
    # path (set_room / set_light) used to make a get_* lookup call before every write,
    # doubling bridge latency; with the cache warm it makes just the write call.

    def _fresh(self, at: float) -> bool:
        return (self._clock() - at) < self._cache_ttl_s

    def _refresh_room_map(self) -> dict:
        rooms = self._call(self._bridge().get_rooms)
        mapping = {r.get("id"): self._grouped_light_id(r) for r in rooms}
        with self._cache_lock:
            self._room_gl, self._room_gl_at = mapping, self._clock()
        return mapping

    def _refresh_light_ids(self) -> set:
        lights = self._call(self._bridge().get_lights)
        ids = {l.get("id") for l in lights}
        with self._cache_lock:
            self._light_ids, self._light_ids_at = ids, self._clock()
        return ids

    def _remember_rooms(self, rooms: list) -> None:
        mapping = {r.get("id"): self._grouped_light_id(r) for r in rooms}
        with self._cache_lock:
            self._room_gl, self._room_gl_at = mapping, self._clock()

    def _remember_lights(self, lights: list) -> None:
        with self._cache_lock:
            self._light_ids = {l.get("id") for l in lights}
            self._light_ids_at = self._clock()

    def invalidate_cache(self) -> None:
        with self._cache_lock:
            self._room_gl = self._light_ids = None

    def _room_grouped_light_id(self, room_id: str) -> str | None:
        """Resolve room_id -> grouped_light id from cache (refresh-on-miss). Raises
        not_found if the room is absent even after a fresh fetch; returns None if the
        room exists but has no grouped_light (caller maps that to `unsupported`)."""
        with self._cache_lock:
            mapping = self._room_gl if (self._room_gl is not None and self._fresh(self._room_gl_at)) else None
        if mapping is None or room_id not in mapping:
            mapping = self._refresh_room_map()  # cold, stale, or unknown id -> one fetch
        if room_id not in mapping:
            raise HueMCPError("not_found", f"room not found: {room_id}")
        return mapping[room_id]

    def _light_exists(self, light_id: str) -> bool:
        with self._cache_lock:
            ids = self._light_ids if (self._light_ids is not None and self._fresh(self._light_ids_at)) else None
        if ids is None or light_id not in ids:
            ids = self._refresh_light_ids()  # cold, stale, or unknown id -> one fetch
        return light_id in ids

    # --- status ---

    def status(self) -> dict:
        out = {"paired": self.paired, "bridge_ip": self._bridge_ip}
        if not self.paired:
            return out
        try:
            b = self._bridge()
            out["lights"] = len(self._call(b.get_lights))
            out["rooms"] = len(self._call(b.get_rooms))
            out["scenes"] = len(self._call(b.get_scenes))
            out["reachable"] = True
        except HueMCPError as exc:
            out["reachable"] = False
            out["error"] = exc.message
        return out

    # --- lights ---

    def list_lights(self) -> list[dict]:
        b = self._bridge()
        lights = self._call(b.get_lights)
        self._remember_lights(lights)  # warm the write-path cache for free
        return [self._light_summary(l) for l in lights]

    def get_light(self, light_id: str) -> dict:
        b = self._bridge()
        light = next((l for l in self._call(b.get_lights) if l.get("id") == light_id), None)
        if light is None:
            raise HueMCPError("not_found", f"light not found: {light_id}")
        return self._light_detail(light)

    def set_light(self, light_id: str, *, on=None, brightness=None,
                  color_xy=None, color_temperature_mirek=None) -> dict:
        if on is None and brightness is None and color_xy is None and color_temperature_mirek is None:
            raise HueMCPError("invalid_argument",
                              "specify at least one of: on, brightness, color_xy, color_temperature_mirek")
        # Validate/coerce bounds before anything reaches the bridge (invalid_argument on
        # bad input), since server.py does not enforce the inputSchema min/max.
        brightness = _validate_brightness(brightness)
        color_temperature_mirek = _validate_mirek(color_temperature_mirek)
        color_xy = _validate_xy(color_xy)
        # Existence check via the cached light-id set (refresh-on-miss) instead of a
        # get_lights() call on every write — gives a clean not_found without the round-trip.
        if not self._light_exists(light_id):
            raise HueMCPError("not_found", f"light not found: {light_id}")
        b = self._bridge()
        if on is not None:
            self._call(b.set_light, light_id, "on", {"on": bool(on)})
        if brightness is not None:
            self._call(b.set_light, light_id, "dimming", {"brightness": brightness})
        if color_xy is not None:
            self._call(b.set_light, light_id, "color", {"xy": color_xy})
        if color_temperature_mirek is not None:
            self._call(b.set_light, light_id, "color_temperature", {"mirek": color_temperature_mirek})
        return {"success": True, "id": light_id}

    # --- rooms ---

    def list_rooms(self) -> list[dict]:
        b = self._bridge()
        rooms = self._call(b.get_rooms)
        self._remember_rooms(rooms)  # warm the write-path cache for free
        gls = {g.get("id"): g for g in self._call(b.get_grouped_lights)}
        out = []
        for r in rooms:
            gl_id = self._grouped_light_id(r)
            gl = gls.get(gl_id, {})
            out.append({
                "id": r.get("id"),
                "name": self._name(r),
                "grouped_light_id": gl_id,
                "device_count": len([c for c in r.get("children", []) if c.get("rtype") == "device"]),
                "on": (gl.get("on") or {}).get("on"),
                "brightness": (gl.get("dimming") or {}).get("brightness"),
            })
        return out

    def get_room(self, room_id: str) -> dict:
        b = self._bridge()
        room = next((r for r in self._call(b.get_rooms) if r.get("id") == room_id), None)
        if room is None:
            raise HueMCPError("not_found", f"room not found: {room_id}")
        child_devices = {c.get("rid") for c in room.get("children", []) if c.get("rtype") == "device"}
        lights = [self._light_summary(l) for l in self._call(b.get_lights)
                  if (l.get("owner") or {}).get("rid") in child_devices]
        gl_id = self._grouped_light_id(room)
        gls = {g.get("id"): g for g in self._call(b.get_grouped_lights)}
        gl = gls.get(gl_id, {})
        return {
            "id": room_id,
            "name": self._name(room),
            "grouped_light_id": gl_id,
            "on": (gl.get("on") or {}).get("on"),
            "brightness": (gl.get("dimming") or {}).get("brightness"),
            "lights": lights,
        }

    def set_room(self, room_id: str, *, on=None, brightness=None) -> dict:
        if on is None and brightness is None:
            raise HueMCPError("invalid_argument", "specify at least one of: on, brightness")
        brightness = _validate_brightness(brightness)
        # Resolve room -> grouped_light from cache (refresh-on-miss): no get_rooms() call
        # on the warm write path. Raises not_found; None means no grouped_light.
        gl_id = self._room_grouped_light_id(room_id)
        if not gl_id:
            raise HueMCPError("unsupported", f"room {room_id} has no grouped_light to control")
        props: dict = {}
        if on is not None:
            props["on"] = {"on": bool(on)}
        if brightness is not None:
            props["dimming"] = {"brightness": brightness}
        self._call(self._bridge().set_grouped_light_service, gl_id, props)
        return {"success": True, "room_id": room_id, "grouped_light_id": gl_id}

    # --- scenes ---

    def list_scenes(self) -> list[dict]:
        b = self._bridge()
        room_names = {r.get("id"): self._name(r) for r in self._call(b.get_rooms)}
        out = []
        for s in self._call(b.get_scenes):
            group = s.get("group") or {}
            rid = group.get("rid")
            out.append({
                "id": s.get("id"),
                "name": self._name(s),
                "room_id": rid,
                "room_name": room_names.get(rid),
            })
        return out

    def activate_scene(self, scene_id: str) -> dict:
        b = self._bridge()
        if not any(s.get("id") == scene_id for s in self._call(b.get_scenes)):
            raise HueMCPError("not_found", f"scene not found: {scene_id}")
        self._call(b.set_scene, scene_id, "recall", {"action": "active"})
        return {"success": True, "scene_id": scene_id}
