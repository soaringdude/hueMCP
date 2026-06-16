"""Test fixtures: an in-memory fake Hue bridge so no real bridge is needed.

The fake mirrors the CLIP v2 resource dicts and the python-hue-v2 bridge method
surface (get_lights / get_rooms / get_grouped_lights / get_scenes / set_light /
set_grouped_light_service / set_scene). It records mutating calls for assertions.
"""

from __future__ import annotations

import copy

import pytest

from huemcp.client import HueBackend
from huemcp.server import create_app

TOKEN = "test-token"


def _seed():
    lights = [
        {"id": "light-1", "type": "light", "owner": {"rid": "device-1", "rtype": "device"},
         "metadata": {"name": "Lamp"}, "on": {"on": True}, "dimming": {"brightness": 80.0},
         "color_temperature": {"mirek": 300}, "color": {"xy": {"x": 0.4, "y": 0.4}}},
        {"id": "light-2", "type": "light", "owner": {"rid": "device-2", "rtype": "device"},
         "metadata": {"name": "Ceiling"}, "on": {"on": False}, "dimming": {"brightness": 20.0}},
    ]
    rooms = [
        {"id": "room-1", "type": "room", "metadata": {"name": "Living Room"},
         "children": [{"rid": "device-1", "rtype": "device"}, {"rid": "device-2", "rtype": "device"}],
         "services": [{"rid": "gl-1", "rtype": "grouped_light"}]},
    ]
    grouped = [{"id": "gl-1", "type": "grouped_light", "owner": {"rid": "room-1", "rtype": "room"},
                "on": {"on": True}, "dimming": {"brightness": 50.0}}]
    scenes = [{"id": "scene-1", "type": "scene", "metadata": {"name": "Relax"},
               "group": {"rid": "room-1", "rtype": "room"}}]
    return lights, rooms, grouped, scenes


class FakeBridge:
    def __init__(self):
        self.lights, self.rooms, self.grouped, self.scenes = _seed()
        self.calls: list = []
        self.gets = {"lights": 0, "rooms": 0, "grouped": 0, "scenes": 0}

    # getters return raw v2 dicts (copies, like a real HTTP fetch); count fetches so
    # tests can assert the write-path cache avoids redundant lookups.
    def get_lights(self):
        self.gets["lights"] += 1
        return copy.deepcopy(self.lights)

    def get_rooms(self):
        self.gets["rooms"] += 1
        return copy.deepcopy(self.rooms)

    def get_grouped_lights(self):
        self.gets["grouped"] += 1
        return copy.deepcopy(self.grouped)

    def get_scenes(self):
        self.gets["scenes"] += 1
        return copy.deepcopy(self.scenes)

    # setters mutate in-memory state and record the call
    def set_light(self, light_id, prop, value):
        self.calls.append(("set_light", light_id, prop, value))
        for l in self.lights:
            if l["id"] == light_id:
                l[prop] = value
        return {"data": [{"rid": light_id}]}

    def set_grouped_light_service(self, gl_id, props):
        self.calls.append(("set_grouped_light_service", gl_id, props))
        for g in self.grouped:
            if g["id"] == gl_id:
                g.update(props)
        return {"data": [{"rid": gl_id}]}

    def set_scene(self, scene_id, prop, value):
        self.calls.append(("set_scene", scene_id, prop, value))
        return {"data": [{"rid": scene_id}]}


class FakeHue:
    def __init__(self, bridge):
        self.bridge = bridge


@pytest.fixture
def fake_bridge():
    return FakeBridge()


@pytest.fixture
def backend(fake_bridge):
    return HueBackend(bridge_ip="192.0.2.5", app_key="test-key",
                      hue_factory=lambda ip, key: FakeHue(fake_bridge))


@pytest.fixture
def make_backend(fake_bridge):
    """Build a backend wired to the same fake_bridge, with controllable cache TTL/clock."""
    def _make(cache_ttl_s=600.0, clock=None):
        return HueBackend(bridge_ip="192.0.2.5", app_key="test-key",
                          hue_factory=lambda ip, key: FakeHue(fake_bridge),
                          cache_ttl_s=cache_ttl_s, clock=clock)
    return _make


@pytest.fixture
def unpaired_backend():
    return HueBackend(bridge_ip=None, app_key=None)


@pytest.fixture
def token():
    return TOKEN


@pytest.fixture
def client(backend):
    from starlette.testclient import TestClient
    return TestClient(create_app(backend, TOKEN, cert_pem="FAKE-PEM"))
