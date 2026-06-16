"""hueMCP tests: pure dispatch(), backend behavior via the fake bridge, and HTTP."""

from __future__ import annotations

import json

from huemcp.server import dispatch


def _rpc(method, params=None, id_=1):
    p = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        p["params"] = params
    return p


def _call(backend, name, arguments=None):
    resp = dispatch(_rpc("tools/call", {"name": name, "arguments": arguments or {}}), backend)
    result = resp["result"]
    data = json.loads(result["content"][0]["text"])
    return data, bool(result.get("isError"))


# --- protocol ----------------------------------------------------------------

def test_initialize(backend):
    resp = dispatch(_rpc("initialize"), backend)
    assert resp["result"]["serverInfo"]["name"] == "hueMCP"


def test_unknown_method(backend):
    assert dispatch(_rpc("nope"), backend)["error"]["code"] == -32601


def test_unknown_tool(backend):
    data, is_err = _call(backend, "ghost")
    assert is_err and data["kind"] == "invalid_argument"


# --- lights ------------------------------------------------------------------

def test_list_lights(backend):
    data, is_err = _call(backend, "list_lights")
    assert not is_err
    names = {l["name"] for l in data}
    assert names == {"Lamp", "Ceiling"}


def test_get_light_not_found(backend):
    data, is_err = _call(backend, "get_light", {"light_id": "nope"})
    assert is_err and data["kind"] == "not_found"


def test_set_light_requires_a_field(backend):
    data, is_err = _call(backend, "set_light", {"light_id": "light-1"})
    assert is_err and data["kind"] == "invalid_argument"


def test_set_light_on(backend, fake_bridge):
    data, is_err = _call(backend, "set_light", {"light_id": "light-2", "on": True, "brightness": 55})
    assert not is_err and data["success"]
    assert ("set_light", "light-2", "on", {"on": True}) in fake_bridge.calls
    assert ("set_light", "light-2", "dimming", {"brightness": 55.0}) in fake_bridge.calls


# --- rooms -------------------------------------------------------------------

def test_list_rooms(backend):
    data, is_err = _call(backend, "list_rooms")
    assert not is_err
    room = data[0]
    assert room["name"] == "Living Room"
    assert room["grouped_light_id"] == "gl-1"
    assert room["on"] is True and room["brightness"] == 50.0


def test_get_room_lists_its_lights(backend):
    data, is_err = _call(backend, "get_room", {"room_id": "room-1"})
    assert not is_err
    assert {l["name"] for l in data["lights"]} == {"Lamp", "Ceiling"}


def test_set_room(backend, fake_bridge):
    data, is_err = _call(backend, "set_room", {"room_id": "room-1", "brightness": 10})
    assert not is_err and data["grouped_light_id"] == "gl-1"
    assert ("set_grouped_light_service", "gl-1", {"dimming": {"brightness": 10.0}}) in fake_bridge.calls


def test_set_room_not_found(backend):
    data, is_err = _call(backend, "set_room", {"room_id": "nope", "on": True})
    assert is_err and data["kind"] == "not_found"


# --- scenes ------------------------------------------------------------------

def test_list_scenes(backend):
    data, is_err = _call(backend, "list_scenes")
    assert not is_err
    assert data[0]["name"] == "Relax" and data[0]["room_name"] == "Living Room"


def test_activate_scene(backend, fake_bridge):
    data, is_err = _call(backend, "activate_scene", {"scene_id": "scene-1"})
    assert not is_err and data["success"]
    assert ("set_scene", "scene-1", "recall", {"action": "active"}) in fake_bridge.calls


def test_activate_scene_not_found(backend):
    data, is_err = _call(backend, "activate_scene", {"scene_id": "nope"})
    assert is_err and data["kind"] == "not_found"


# --- pairing / status --------------------------------------------------------

def test_status_paired(backend):
    data, _ = _call(backend, "status")
    assert data["paired"] is True and data["reachable"] is True
    assert data["lights"] == 2 and data["rooms"] == 1 and data["scenes"] == 1


def test_unpaired_status(unpaired_backend):
    data, _ = _call(unpaired_backend, "status")
    assert data["paired"] is False


def test_unpaired_tool_errors(unpaired_backend):
    data, is_err = _call(unpaired_backend, "list_lights")
    assert is_err and data["kind"] == "hue_unreachable"


# --- HTTP --------------------------------------------------------------------

def test_unauthorized(client):
    assert client.post("/mcp", json=_rpc("tools/list")).status_code == 401


def test_authorized_tools_list(client, token):
    r = client.post("/mcp", json=_rpc("tools/list"), headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"list_lights", "set_light", "list_rooms", "set_room", "list_scenes", "activate_scene"} <= names


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["server"] == "hueMCP" and body["paired"] is True and body["status"] == "ok"
