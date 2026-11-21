import json
import socket
from http.client import HTTPConnection

import httpx
import pytest
import respx

from technitium_sync.health import HealthState, clean_reason, start_health_server
from technitium_sync.main import Syncer, healthcheck
from technitium_sync.mapping import MAX_ITEMS
from technitium_sync.std_client import StdClient
from technitium_sync.technitium import TechnitiumClient
from tests.conftest import STD, TECHNITIUM, load_fixture
from tests.test_main import APPLIED, SNAPSHOT, _technitium


class Clock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def state(clock):
    return HealthState(max_age=900, stream_id="technitium-dhcp", clock=clock)


# --- verdict logic -----------------------------------------------------------------

def test_starting_before_any_cycle(state):
    assert state.verdict() == (False, "starting")


def test_failure_before_first_success_is_the_reason(state):
    state.record_failure("technitium: unreachable")
    assert state.verdict() == (False, "technitium: unreachable")


def test_fresh_success_is_ok(state, clock):
    state.record_success(3)
    clock.now += 899
    assert state.verdict() == (True, "OK")


def test_stale_success(state, clock):
    state.record_success(3)
    clock.now += 22 * 60 + 5
    assert state.verdict() == (False, "last push 22m ago")


def test_stale_under_a_minute_uses_seconds(clock):
    state = HealthState(max_age=10, clock=clock)
    state.record_success(0)
    clock.now += 45
    assert state.verdict() == (False, "last push 45s ago")


def test_failure_after_success_wins_over_recent_success(state, clock):
    state.record_success(3)
    clock.now += 60
    state.record_failure("std: 401 check STD_INGEST_KEY")
    assert state.verdict() == (False, "std: 401 check STD_INGEST_KEY")
    state.record_success(3)
    assert state.verdict() == (True, "OK")


def test_clean_reason_one_line_capped_and_scrubbed():
    text = clean_reason("a\nb\n\n  c " + "x" * 500, secrets=["xxxx"])
    assert "\n" not in text and len(text) <= 200
    assert clean_reason("bad tok-123 here", secrets=["tok-123"]) == "bad *** here"


def test_status_fields(state, clock):
    state.record_success(7)
    clock.now += 5
    state.record_failure("technitium: invalid-token")
    s = state.status()
    assert s["stream_id"] == "technitium-dhcp"
    assert s["last_error"] == "technitium: invalid-token"
    assert s["last_item_count"] == 7
    assert s["last_success_at"] == "2023-11-14T22:13:20Z"
    assert s["last_attempt_at"] == "2023-11-14T22:13:25Z"
    assert s["health_max_age_seconds"] == 900
    assert s["healthy"] is False and s["version"]


# --- the loop feeds the state ------------------------------------------------------

@pytest.fixture
def syncer(settings, state):
    t = TechnitiumClient(TECHNITIUM, "tech-token")
    s = StdClient(STD, "std_abc_secret")
    yield Syncer(settings, t, s, state)
    t.close()
    s.close()


@pytest.fixture
def std():
    with respx.mock(base_url=STD, assert_all_called=False) as m:
        m.get("/api/v1/meta").respond(200, json=load_fixture("std_meta.json"))
        m.get("/api/v1/streams/technitium-dhcp/status").respond(404)
        m.put(SNAPSHOT, name="put").respond(200, json=APPLIED)
        yield m


def test_cycle_success_then_technitium_failure(syncer, std, state):
    _technitium(std)
    syncer.run_once()
    assert state.verdict() == (True, "OK")
    assert state.last_item_count == 3
    std.get(f"{TECHNITIUM}/api/dhcp/scopes/list").respond(401)
    syncer.run_once()
    assert state.verdict() == (False, "technitium: 401 unauthorized")
    std.get(f"{TECHNITIUM}/api/dhcp/scopes/list").respond(200, json=load_fixture("invalid_token.json"))
    syncer.run_once()
    assert state.verdict() == (False, "technitium: invalid-token")
    std.get(f"{TECHNITIUM}/api/dhcp/scopes/list").mock(side_effect=httpx.ConnectError("down"))
    syncer.run_once()
    assert state.verdict() == (False, "technitium: unreachable")


def test_cycle_success_then_std_failures(syncer, std, state):
    _technitium(std)
    syncer.run_once()
    std["put"].respond(422, json={"error": "Unprocessable", "details": "bad item"})
    syncer.run_once()
    assert state.verdict() == (False, "std: 422 Unprocessable — bad item")
    std["put"].respond(401, json={"error": "nope"})
    syncer.run_once()
    assert state.verdict() == (False, "std: 401 check STD_INGEST_KEY")
    std["put"].mock(side_effect=httpx.ConnectError("down"))
    syncer.run_once()
    assert state.verdict() == (False, "std: unreachable")
    std["put"].respond(200, json=APPLIED)
    syncer.run_once()
    assert state.verdict() == (True, "OK")


def test_202_pending_counts_as_success(syncer, std, state):
    _technitium(std)
    std["put"].respond(202, json={"stream_status": "pending", "applied": False, "item_count": 3})
    syncer.run_once()
    assert state.verdict() == (True, "OK")


def test_std_too_old(syncer, std, state):
    std.get("/api/v1/meta").respond(200, json={"std_version": "0.9.0", "api_revision": 1, "features": []})
    syncer.run_once()
    assert state.verdict() == (False, "std: streams not supported (needs STD 0.10.0)")


def test_too_many_items(syncer, std, state):
    leases = [{"hostName": f"h{n}", "hardwareAddress": f"{n:012x}", "address": "10.0.0.1",
               "comments": ""} for n in range(MAX_ITEMS + 1)]
    empty = {"status": "ok", "response": {"reservedLeases": []}}
    _technitium(std, by_name={"LAN": {"status": "ok", "response": {"reservedLeases": leases}},
                              "IoT": empty})
    syncer.run_once()
    assert state.verdict() == (False, "snapshot too large (5001 items > 5000)")


def test_tokens_never_in_body(settings, std):
    secrets = ["tech-token", "std_abc_secret"]
    state = HealthState(secrets=secrets)
    t = TechnitiumClient(TECHNITIUM, "tech-token")
    s = StdClient(STD, "std_abc_secret")
    syncer = Syncer(settings, t, s, state)
    _technitium(std)
    # STD echoing the key back must not leak it through /healthz.
    std["put"].respond(422, json={"error": "bad key std_abc_secret", "details": "tech-token"})
    syncer.run_once()
    body = state.verdict()[1]
    assert body.startswith("std: 422")
    assert not any(secret in body for secret in secrets)
    assert not any(secret in json.dumps(state.status()) for secret in secrets)
    t.close()
    s.close()


# --- real HTTP round trip ----------------------------------------------------------

@pytest.fixture
def server(state):
    srv = start_health_server(state, 0, host="127.0.0.1")
    yield srv
    srv.shutdown()
    srv.server_close()


def _req(server, method, path):
    conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.request(method, path)
    resp = conn.getresponse()
    data = resp.read().decode()
    conn.close()
    return resp, data


def test_http_healthz_round_trip(server, state, clock):
    resp, body = _req(server, "GET", "/healthz")
    assert (resp.status, body) == (503, "starting")
    assert resp.getheader("Content-Type") == "text/plain; charset=utf-8"
    state.record_success(3)
    resp, body = _req(server, "GET", "/healthz")
    assert (resp.status, body) == (200, "OK")
    state.record_failure("std: unreachable")
    assert _req(server, "GET", "/healthz")[1] == "std: unreachable"
    clock.now += 1000
    state.record_success(3)
    clock.now += 1000
    assert _req(server, "GET", "/healthz")[1] == "last push 16m ago"


def test_http_status_json(server, state):
    state.record_success(3)
    resp, body = _req(server, "GET", "/status")
    assert resp.status == 200 and resp.getheader("Content-Type") == "application/json"
    data = json.loads(body)
    assert data["healthy"] is True and data["last_item_count"] == 3 and data["last_error"] is None


def test_http_404_and_405(server):
    assert _req(server, "GET", "/nope")[0].status == 404
    assert _req(server, "POST", "/healthz")[0].status == 405
    assert _req(server, "PUT", "/healthz")[0].status == 405
    assert _req(server, "HEAD", "/healthz")[0].status == 503


# --- the Docker healthcheck subcommand ---------------------------------------------

def test_healthcheck_exit_codes(server, state, capsys):
    env = {"HEALTH_PORT": str(server.server_address[1])}
    assert healthcheck(env) == 1
    assert capsys.readouterr().out.strip() == "starting"
    state.record_success(1)
    assert healthcheck(env) == 0
    state.record_failure("technitium: unreachable")
    assert healthcheck(env) == 1
    assert "technitium: unreachable" in capsys.readouterr().out


def test_healthcheck_nothing_listening(capsys):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert healthcheck({"HEALTH_PORT": str(port)}) == 1
    assert "not reachable" in capsys.readouterr().out
