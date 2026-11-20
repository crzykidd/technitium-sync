import json
import os
import time

import httpx
import pytest
import respx

from technitium_sync.main import Backoff, Syncer, healthcheck
from technitium_sync.mapping import MAX_ITEMS
from technitium_sync.std_client import StdClient
from technitium_sync.technitium import TechnitiumClient
from tests.conftest import STD, TECHNITIUM, load_fixture

SNAPSHOT = "/api/v1/streams/technitium-dhcp/snapshot"
APPLIED = {"stream_id": "technitium-dhcp", "stream_status": "enabled", "applied": True,
           "item_count": 0, "counts": {}}


@pytest.fixture
def syncer(settings):
    t = TechnitiumClient(TECHNITIUM, "tech-token")
    s = StdClient(STD, "std_abc_secret")
    yield Syncer(settings, t, s)
    t.close()
    s.close()


@pytest.fixture
def std():
    with respx.mock(base_url=STD, assert_all_called=False) as m:
        m.get("/api/v1/meta", name="meta").respond(200, json=load_fixture("std_meta.json"))
        m.get("/api/v1/streams/technitium-dhcp/status", name="status").respond(404)
        m.put(SNAPSHOT, name="put").respond(200, json=APPLIED)
        yield m


def _technitium(mock, scopes=None, by_name=None):
    mock.get(f"{TECHNITIUM}/api/dhcp/scopes/list").respond(
        200, json=scopes or load_fixture("scopes_list.json"))
    by_name = by_name or {"LAN": load_fixture("scope_lan.json"),
                          "IoT": load_fixture("scope_iot.json")}

    def get(request):
        return httpx.Response(200, json=by_name[request.url.params["name"]])

    mock.get(f"{TECHNITIUM}/api/dhcp/scopes/get").mock(side_effect=get)


def _sent(std) -> dict:
    return json.loads(std["put"].calls[-1].request.content)


def test_full_run_sends_snapshot_and_touches_heartbeat(syncer, std, settings):
    _technitium(std)
    assert syncer.run_once() == settings.interval_seconds
    body = _sent(std)
    assert body["producer"] == "technitium-sync"
    assert body["key_type"] == "mac"
    assert body["display_name"] == "DHCP"
    names = [i["name"] for i in body["items"]]
    # LAN: nas, printer (null hostname skipped); IoT: shelly-pump (duplicate nas MAC dropped)
    assert names == ["nas", "printer", "shelly-pump"]
    assert os.path.exists(settings.heartbeat_file)
    assert std["meta"].call_count == 1 and std["status"].call_count == 1


def test_meta_checked_only_until_ready(syncer, std):
    _technitium(std)
    syncer.run_once()
    syncer.run_once()
    assert std["meta"].call_count == 1
    assert std["put"].call_count == 2


def test_technitium_failure_sends_nothing(syncer, std, settings):
    std.get(f"{TECHNITIUM}/api/dhcp/scopes/list").respond(200, json=load_fixture("invalid_token.json"))
    assert syncer.run_once() == settings.interval_seconds
    assert not std["put"].called
    assert not os.path.exists(settings.heartbeat_file)


def test_technitium_unreachable_sends_nothing(syncer, std):
    std.get(f"{TECHNITIUM}/api/dhcp/scopes/list").mock(side_effect=httpx.ConnectError("down"))
    syncer.run_once()
    assert not std["put"].called


def test_really_empty_sends_empty_list(syncer, std, caplog):
    empty = {"status": "ok", "response": {"reservedLeases": []}}
    _technitium(std, by_name={"LAN": empty, "IoT": empty})
    syncer.run_once()
    assert _sent(std)["items"] == []
    assert "empty snapshot" in caplog.text


def test_over_item_limit_sends_nothing(syncer, std, caplog):
    leases = [{"hostName": f"h{n}", "hardwareAddress": f"{n:012x}", "address": "10.0.0.1",
               "comments": ""} for n in range(MAX_ITEMS + 1)]
    big = {"status": "ok", "response": {"reservedLeases": leases}}
    empty = {"status": "ok", "response": {"reservedLeases": []}}
    _technitium(std, by_name={"LAN": big, "IoT": empty})
    syncer.run_once()
    assert not std["put"].called
    assert "over STD's limit of 5000" in caplog.text


def test_std_too_old_waits_normal_interval(syncer, std, settings):
    std["meta"].respond(200, json={"std_version": "0.9.0", "api_revision": 1, "features": []})
    assert syncer.run_once() == settings.interval_seconds
    assert not std["put"].called


def test_5xx_backs_off_then_resets(syncer, std, settings):
    _technitium(std)
    std["put"].mock(side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(500),
                                 httpx.Response(200, json=APPLIED)])
    assert [syncer.run_once() for _ in range(4)] == [5, 10, 20, settings.interval_seconds]
    assert std["put"].call_count == 4


def test_400_waits_normal_interval(syncer, std, settings):
    _technitium(std)
    std["put"].respond(400, json={"error": "Invalid request body", "details": []})
    assert syncer.run_once() == settings.interval_seconds
    assert not os.path.exists(settings.heartbeat_file)


def test_202_pending_touches_heartbeat(syncer, std, settings):
    _technitium(std)
    std["put"].respond(202, json={"stream_status": "pending", "applied": False, "item_count": 3})
    syncer.run_once()
    assert os.path.exists(settings.heartbeat_file)


def test_backoff_caps():
    b = Backoff(cap=60)
    assert [b.next() for _ in range(6)] == [5, 10, 20, 40, 60, 60]
    b.reset()
    assert b.next() == 5


def test_healthcheck(tmp_path):
    hb = tmp_path / "hb"
    env = {"HEARTBEAT_FILE": str(hb), "INTERVAL_SECONDS": "60"}
    assert healthcheck(env) == 1  # no file yet
    hb.write_text("x")
    assert healthcheck(env) == 0
    assert healthcheck(env, now=time.time() + 181) == 1
    assert healthcheck(env, now=time.time() + 170) == 0
