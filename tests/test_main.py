import json
import os

import httpx
import pytest
import respx

from technitium_sync.main import Backoff, Syncer
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


def _revision(std, revision, version="0.10.1"):
    """Make every STD response carry this API revision."""
    headers = {"X-STD-API-Revision": str(revision), "X-STD-Version": version}
    std["meta"].respond(200, json=dict(load_fixture("std_meta.json"), api_revision=revision),
                        headers=headers)
    std["put"].respond(200, json=APPLIED, headers=headers)


BASE_KEYS = {"display_name", "producer", "producer_version", "key_type", "items"}


@pytest.mark.parametrize("revision, sent", [(2, False), (3, True), (4, True)])
def test_interval_sent_only_from_revision_3(syncer, std, settings, revision, sent):
    _technitium(std)
    _revision(std, revision)
    syncer.run_once()
    body = _sent(std)
    assert set(body) == BASE_KEYS | ({"snapshot_interval_seconds"} if sent else set())
    if sent:
        assert body["snapshot_interval_seconds"] == settings.interval_seconds


def test_interval_absent_when_revision_missing_or_garbage(syncer, std):
    _technitium(std)
    std["meta"].respond(200, json={"std_version": "0.10.0", "api_revision": "x",
                                   "features": ["streams"]}, headers={"X-STD-API-Revision": "nope"})
    syncer.run_once()
    assert set(_sent(std)) == BASE_KEYS


def test_revision_2_to_3_mid_run_sends_from_next_snapshot(syncer, std):
    _technitium(std)
    _revision(std, 2)
    std["put"].mock(side_effect=[
        httpx.Response(200, json=APPLIED, headers={"X-STD-API-Revision": "3"}),
        httpx.Response(200, json=APPLIED, headers={"X-STD-API-Revision": "3"})])
    syncer.run_once()
    assert "snapshot_interval_seconds" not in _sent(std)  # sent while still on 2
    syncer.run_once()
    assert "snapshot_interval_seconds" in _sent(std)
    assert std["meta"].call_count == 1  # no per-run meta call


def test_revision_3_to_2_drops_the_field(syncer, std):
    _technitium(std)
    _revision(std, 3)
    std["put"].mock(side_effect=[
        httpx.Response(200, json=APPLIED, headers={"X-STD-API-Revision": "2"}),
        httpx.Response(200, json=APPLIED, headers={"X-STD-API-Revision": "2"})])
    syncer.run_once()
    assert "snapshot_interval_seconds" in _sent(std)
    syncer.run_once()
    assert "snapshot_interval_seconds" not in _sent(std)


@pytest.mark.parametrize("code", [400, 409, 413])
def test_rejection_rechecks_meta_and_a_downgrade_drops_the_field(syncer, std, settings, code):
    _technitium(std)
    _revision(std, 3)
    std["put"].mock(side_effect=[httpx.Response(code, json={"error": "no", "details": None}),
                                 httpx.Response(200, json=APPLIED)])
    assert syncer.run_once() == settings.interval_seconds  # normal schedule
    assert "snapshot_interval_seconds" in _sent(std)
    assert std["meta"].call_count == 1
    std["meta"].respond(200, json=dict(load_fixture("std_meta.json"), api_revision=2),
                        headers={"X-STD-API-Revision": "2"})  # STD was downgraded
    syncer.run_once()
    assert std["meta"].call_count == 2
    assert "snapshot_interval_seconds" not in _sent(std)
    assert std["status"].call_count == 1  # the stream status is only logged at startup


def test_no_meta_recheck_after_a_good_push(syncer, std):
    _technitium(std)
    syncer.run_once()
    syncer.run_once()
    assert std["meta"].call_count == 1


def test_failed_meta_recheck_pushes_nothing(syncer, std, settings):
    _technitium(std)
    _revision(std, 3)
    std["put"].respond(400, json={"error": "no", "details": None})
    syncer.run_once()
    assert std["put"].call_count == 1
    std["meta"].mock(side_effect=httpx.ConnectError("refused"))
    assert syncer.run_once() == 5  # existing retry and backoff
    assert std["put"].call_count == 1
    std["meta"].respond(200, json=load_fixture("std_meta.json"))
    assert syncer.run_once() == settings.interval_seconds
    assert std["put"].call_count == 2


def test_failed_startup_meta_pushes_nothing(syncer, std):
    std["meta"].mock(side_effect=httpx.ConnectError("refused"))
    assert syncer.run_once() == 5
    assert not std["put"].called


@pytest.mark.parametrize("status", ["pending", "disabled", "ignored"])
def test_202_keeps_the_normal_schedule(syncer, std, settings, status):
    _technitium(std)
    std["put"].respond(202, json={"stream_status": status, "applied": False, "item_count": 3})
    assert [syncer.run_once() for _ in range(3)] == [settings.interval_seconds] * 3
    assert std["put"].call_count == 3  # a full snapshot every interval, whatever the status


def test_interval_over_std_maximum_is_clamped_with_one_warning(settings, std, caplog):
    _technitium(std)
    _revision(std, 3)
    s = settings.model_copy(update={"interval_seconds": 100000})
    t = TechnitiumClient(TECHNITIUM, "tech-token")
    c = StdClient(STD, "std_abc_secret")
    try:
        syncer = Syncer(s, t, c)
        syncer.run_once()
        syncer.run_once()
    finally:
        t.close()
        c.close()
    assert _sent(std)["snapshot_interval_seconds"] == 86400
    assert sum("over STD's maximum of 86400" in r.getMessage() for r in caplog.records) == 1
