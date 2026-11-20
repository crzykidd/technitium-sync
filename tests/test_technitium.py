import httpx
import pytest
import respx

from technitium_sync.technitium import TechnitiumClient, TechnitiumError, read_reservations
from tests.conftest import TECHNITIUM, load_fixture

SCOPES = load_fixture("scopes_list.json")
BY_NAME = {
    "LAN": load_fixture("scope_lan.json"),
    "IoT": load_fixture("scope_iot.json"),
    "Lab": load_fixture("scope_lab.json"),
}


def _scope_get(request):
    name = request.url.params["name"]
    if name in BY_NAME:
        return httpx.Response(200, json=BY_NAME[name])
    return httpx.Response(200, json=load_fixture("error_status.json"))


@pytest.fixture
def api():
    with respx.mock(base_url=TECHNITIUM, assert_all_called=False) as mock:
        mock.get("/api/dhcp/scopes/list", name="list").respond(200, json=SCOPES)
        mock.get("/api/dhcp/scopes/get", name="get").mock(side_effect=_scope_get)
        mock.get("/api/dhcp/leases/list", name="leases").respond(500)
        yield mock


@pytest.fixture
def client():
    c = TechnitiumClient(TECHNITIUM, "tech-token")
    yield c
    c.close()


def test_reads_enabled_scopes_only(api, client):
    found = read_reservations(client, [])
    assert {r.scope for r in found} == {"LAN", "IoT"}
    assert len(found) == 5
    assert not api["leases"].called
    requested = [c.request.url.params["name"] for c in api["get"].calls]
    assert requested == ["LAN", "IoT"]


def test_sends_bearer_token_not_query_param(api, client):
    read_reservations(client, ["LAN"])
    request = api["list"].calls[0].request
    assert request.headers["Authorization"] == "Bearer tech-token"
    assert "token" not in request.url.params
    assert request.headers["User-Agent"].startswith("technitium-sync/")


def test_named_disabled_scope_is_read(api, client):
    found = read_reservations(client, ["Lab"])
    assert [r.host_name for r in found] == ["lab-pi"]


def test_configured_scope_missing_is_error(api, client):
    with pytest.raises(TechnitiumError, match="Nope"):
        read_reservations(client, ["LAN", "Nope"])
    assert not api["get"].called  # fails before reading anything


def test_status_error_as_http_200(client):
    with respx.mock(base_url=TECHNITIUM) as mock:
        mock.get("/api/dhcp/scopes/list").respond(200, json=load_fixture("error_status.json"))
        with pytest.raises(TechnitiumError, match="status='error'"):
            client.list_scopes()


def test_invalid_token_as_http_200(client):
    with respx.mock(base_url=TECHNITIUM) as mock:
        mock.get("/api/dhcp/scopes/list").respond(200, json=load_fixture("invalid_token.json"))
        with pytest.raises(TechnitiumError, match="TECHNITIUM_TOKEN"):
            client.list_scopes()


def test_error_in_one_scope_fails_the_whole_read(client):
    with respx.mock(base_url=TECHNITIUM) as mock:
        mock.get("/api/dhcp/scopes/list").respond(200, json=SCOPES)
        mock.get("/api/dhcp/scopes/get", params={"name": "LAN"}).respond(200, json=BY_NAME["LAN"])
        mock.get("/api/dhcp/scopes/get", params={"name": "IoT"}).respond(
            200, json=load_fixture("error_status.json"))
        with pytest.raises(TechnitiumError):
            read_reservations(client, [])


@pytest.mark.parametrize("response", [
    httpx.Response(502),
    httpx.Response(200, text="<html>proxy</html>"),
    httpx.Response(200, json={"status": "ok"}),
])
def test_bad_http_responses(client, response):
    with respx.mock(base_url=TECHNITIUM) as mock:
        mock.get("/api/dhcp/scopes/list").mock(return_value=response)
        with pytest.raises(TechnitiumError):
            client.list_scopes()


def test_network_error(client):
    with respx.mock(base_url=TECHNITIUM) as mock:
        mock.get("/api/dhcp/scopes/list").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(TechnitiumError, match="cannot reach"):
            client.list_scopes()


def test_scope_without_reservations(client):
    empty = {"status": "ok", "response": {"name": "LAN", "reservedLeases": []}}
    with respx.mock(base_url=TECHNITIUM) as mock:
        mock.get("/api/dhcp/scopes/list").respond(200, json=SCOPES)
        mock.get("/api/dhcp/scopes/get").respond(200, json=empty)
        assert read_reservations(client, ["LAN"]) == []
