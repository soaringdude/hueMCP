"""Tool REGISTRY: JSON Schemas for `tools/list` and handlers for `tools/call`.

Each handler receives the injected HueBackend and the raw `arguments` dict, and returns
a JSON-able result. It raises HueMCPError on tool-level failure; any other exception is
caught by dispatch() and wrapped as upstream_error.
"""

from __future__ import annotations

from typing import Callable

from .client import HueBackend

Handler = Callable[[HueBackend, dict], object]

_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_BRIGHTNESS = {"type": "number", "minimum": 0, "maximum": 100,
               "description": "0-100 percent"}

REGISTRY: dict[str, dict] = {}


def _register(name, description, *, properties=None, required=None, mutates=True):
    def decorator(fn: Handler) -> Handler:
        schema = {"type": "object", "properties": properties or {}, "additionalProperties": False}
        if required:
            schema["required"] = required
        REGISTRY[name] = {"description": description, "inputSchema": schema,
                          "handler": fn, "mutates": mutates}
        return fn
    return decorator


def list_tools() -> list[dict]:
    return [{"name": n, "description": e["description"], "inputSchema": e["inputSchema"]}
            for n, e in REGISTRY.items()]


# --- status ------------------------------------------------------------------

@_register("status", "Bridge connection status: paired, reachable, and light/room/scene counts. Read-only.",
           mutates=False)
def _status(b: HueBackend, args: dict) -> dict:
    return b.status()


# --- lights ------------------------------------------------------------------

@_register("list_lights", "List all lights: id, name, on, brightness. Read-only.", mutates=False)
def _list_lights(b: HueBackend, args: dict) -> list:
    return b.list_lights()


@_register("get_light", "Full state of one light (on, brightness, color, color temperature). Read-only.",
           properties={"light_id": _STR}, required=["light_id"], mutates=False)
def _get_light(b: HueBackend, args: dict) -> dict:
    return b.get_light(args["light_id"])


@_register(
    "set_light",
    "Set a light's state. Specify at least one of on / brightness / color_xy / color_temperature_mirek.",
    properties={
        "light_id": _STR,
        "on": _BOOL,
        "brightness": _BRIGHTNESS,
        "color_xy": {
            "type": "object",
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
            "required": ["x", "y"],
            "additionalProperties": False,
            "description": "CIE xy chromaticity, each 0.0-1.0",
        },
        "color_temperature_mirek": {"type": "integer", "minimum": 153, "maximum": 500,
                                    "description": "white color temperature in mirek (153=cool, 500=warm)"},
    },
    required=["light_id"],
)
def _set_light(b: HueBackend, args: dict) -> dict:
    return b.set_light(
        args["light_id"],
        on=args.get("on"),
        brightness=args.get("brightness"),
        color_xy=args.get("color_xy"),
        color_temperature_mirek=args.get("color_temperature_mirek"),
    )


# --- rooms -------------------------------------------------------------------

@_register("list_rooms", "List all rooms: id, name, grouped-light on/brightness, device count. Read-only.",
           mutates=False)
def _list_rooms(b: HueBackend, args: dict) -> list:
    return b.list_rooms()


@_register("get_room", "One room's detail: name, on/brightness, and the lights it contains. Read-only.",
           properties={"room_id": _STR}, required=["room_id"], mutates=False)
def _get_room(b: HueBackend, args: dict) -> dict:
    return b.get_room(args["room_id"])


@_register(
    "set_room",
    "Set a whole room's lights at once (its grouped_light). Specify at least one of on / brightness.",
    properties={"room_id": _STR, "on": _BOOL, "brightness": _BRIGHTNESS},
    required=["room_id"],
)
def _set_room(b: HueBackend, args: dict) -> dict:
    return b.set_room(args["room_id"], on=args.get("on"), brightness=args.get("brightness"))


# --- scenes ------------------------------------------------------------------

@_register("list_scenes", "List all scenes: id, name, and the room each belongs to. Read-only.",
           mutates=False)
def _list_scenes(b: HueBackend, args: dict) -> list:
    return b.list_scenes()


@_register("activate_scene", "Activate (recall) a scene by id. Applies it to its room.",
           properties={"scene_id": _STR}, required=["scene_id"])
def _activate_scene(b: HueBackend, args: dict) -> dict:
    return b.activate_scene(args["scene_id"])
