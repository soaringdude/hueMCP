"""Tests for backend argument validation and on-disk secret permissions.

Bounds (brightness 0-100, mirek 153-500, color_xy x/y 0-1) are enforced in the
backend because server.py does not validate against the inputSchema. Secret files
(state.json, key.pem) must be created 0600, never world-readable.
"""

from __future__ import annotations

import stat

import pytest

from huemcp.client import save_state
from huemcp.errors import HueMCPError
from huemcp.tls import ensure_self_signed

LID = "light-1"
KID = "room-1"


@pytest.mark.parametrize("kwargs", [
    {"brightness": 300},
    {"brightness": -1},
    {"brightness": "bright"},
    {"color_temperature_mirek": 50},
    {"color_temperature_mirek": 999},
    {"color_xy": {"x": 0.3}},
    {"color_xy": {"x": 2.0, "y": 0.3}},
    {"color_xy": [0.3, 0.3]},
])
def test_set_light_out_of_range_is_invalid_argument(backend, kwargs):
    with pytest.raises(HueMCPError) as exc:
        backend.set_light(LID, **kwargs)
    assert exc.value.kind == "invalid_argument"


def test_set_room_out_of_range_is_invalid_argument(backend):
    with pytest.raises(HueMCPError) as exc:
        backend.set_room(KID, brightness=150)
    assert exc.value.kind == "invalid_argument"


def test_set_light_boundary_values_accepted(backend, fake_bridge):
    backend.set_light(LID, brightness=0, color_temperature_mirek=153,
                      color_xy={"x": 0.0, "y": 1.0})
    backend.set_light(LID, brightness=100, color_temperature_mirek=500)
    assert ("set_light", LID, "dimming", {"brightness": 100.0}) in fake_bridge.calls


def test_invalid_argument_raised_before_existence_check(backend, fake_bridge):
    # Bad bounds must fail as invalid_argument even for a nonexistent light (no fetch).
    with pytest.raises(HueMCPError) as exc:
        backend.set_light("no-such-light", brightness=300)
    assert exc.value.kind == "invalid_argument"


def test_save_state_is_0600(tmp_path):
    p = tmp_path / "state.json"
    save_state(p, "192.0.2.5", "secret-app-key")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_save_state_tightens_preexisting_file(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{}")
    p.chmod(0o644)
    save_state(p, "192.0.2.5", "secret-app-key")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_key_pem_is_0600(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    ensure_self_signed(cert, key, host="hueMCP.local")
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
