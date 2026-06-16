"""Tests for the write-path structural cache (room->grouped_light, light ids).

Invariants: writes skip the lookup fetch when the cache is warm; cold/stale/unknown-id
writes refresh exactly once; state reads are never served from cache; the cache expires
on TTL; not_found still works after a forced refresh.
"""

from __future__ import annotations

KID = "room-1"
LID = "light-2"


def test_set_room_warm_cache_skips_get_rooms(backend, fake_bridge):
    backend.list_rooms()                 # warms the room->grouped_light cache
    fake_bridge.gets["rooms"] = 0        # reset the counter
    backend.set_room(KID, brightness=10)
    assert fake_bridge.gets["rooms"] == 0   # no lookup fetch on the warm write path
    assert ("set_grouped_light_service", "gl-1", {"dimming": {"brightness": 10.0}}) in fake_bridge.calls


def test_set_room_cold_refreshes_once(backend, fake_bridge):
    assert fake_bridge.gets["rooms"] == 0
    backend.set_room(KID, on=True)
    assert fake_bridge.gets["rooms"] == 1   # one refresh, then the write


def test_set_light_warm_cache_skips_get_lights(backend, fake_bridge):
    backend.list_lights()
    fake_bridge.gets["lights"] = 0
    backend.set_light(LID, on=True)
    assert fake_bridge.gets["lights"] == 0
    assert ("set_light", LID, "on", {"on": True}) in fake_bridge.calls


def test_cache_expires_on_ttl(make_backend, fake_bridge):
    now = {"t": 1000.0}
    b = make_backend(cache_ttl_s=60.0, clock=lambda: now["t"])
    b.set_room(KID, on=True)
    assert fake_bridge.gets["rooms"] == 1   # cold refresh
    now["t"] += 30                          # still fresh
    b.set_room(KID, on=False)
    assert fake_bridge.gets["rooms"] == 1   # served from cache
    now["t"] += 31                          # now past the 60s TTL
    b.set_room(KID, on=True)
    assert fake_bridge.gets["rooms"] == 2   # refreshed again


def test_unknown_room_forces_refresh_then_not_found(backend, fake_bridge):
    backend.list_rooms()                    # warm with the known room only
    fake_bridge.gets["rooms"] = 0
    import pytest
    from huemcp.errors import HueMCPError
    with pytest.raises(HueMCPError) as ei:
        backend.set_room("ghost", on=True)
    assert ei.value.kind == "not_found"
    assert fake_bridge.gets["rooms"] == 1   # forced one refresh before giving up


def test_reads_are_never_cached(backend, fake_bridge):
    backend.list_rooms()
    backend.list_rooms()
    assert fake_bridge.gets["rooms"] == 2   # every read hits the bridge for live state


def test_invalidate_cache(backend, fake_bridge):
    backend.list_rooms()
    fake_bridge.gets["rooms"] = 0
    backend.invalidate_cache()
    backend.set_room(KID, on=True)
    assert fake_bridge.gets["rooms"] == 1   # cache cleared -> refresh
