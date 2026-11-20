import json
import logging

import httpx
import pytest
import respx

from technitium_sync.std_client import Outcome, StdClient
from tests.conftest import STD, load_fixture

SNAPSHOT = "/api/v1/streams/technitium-dhcp/snapshot"
STATUS = "/api/v1/streams/technitium-dhcp/status"
HEADERS = {"X-STD-API-Revision": "2", "X-STD-Version": "0.10.0"}
BODY = json.dumps({"items": []}).encode()


@pytest.fixture
def client():
    c = StdClient(STD, "std_abc_secret")
    yield c
    c.close()


@pytest.fixture
def mock():
    with respx.mock(base_url=STD) as m:
        yield m


def test_200_logs_counts(client, mock, caplog):
    caplog.set_level(logging.INFO)
    route = mock.put(SNAPSHOT).respond(200, headers=HEADERS, json={
        "stream_id": "technitium-dhcp", "stream_status": "enabled", "applied": True,
        "item_count": 3, "counts": {"created": 1, "updated": 0, "unchanged": 2, "missing": 0}})
    assert client.put_snapshot("technitium-dhcp", BODY) is Outcome.OK
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer std_abc_secret"
    assert request.headers["User-Agent"].startswith("technitium-sync/")
    assert request.headers["Content-Type"] == "application/json"
    assert "Snapshot applied: 3 item(s); created=1, updated=0, unchanged=2, missing=0" in caplog.text


def test_202_pending_warns_once_per_state_change(client, mock, caplog):
    pending = {"stream_id": "technitium-dhcp", "stream_status": "pending", "applied": False,
               "item_count": 3, "message": "Enable it in Settings → Streams"}
    disabled = dict(pending, stream_status="disabled")
    mock.put(SNAPSHOT).mock(side_effect=[
        httpx.Response(202, json=pending), httpx.Response(202, json=pending),
        httpx.Response(202, json=disabled), httpx.Response(202, json=pending),
    ])
    for _ in range(4):
        assert client.put_snapshot("technitium-dhcp", BODY) is Outcome.OK
    warnings = [r for r in caplog.records if "waiting for approval" in r.getMessage()]
    assert len(warnings) == 2  # pending, pending again; the repeat is silent
    disabled_warnings = [r for r in caplog.records if "disabled in STD" in r.getMessage()]
    assert len(disabled_warnings) == 1
    assert all(r.levelno == logging.WARNING for r in warnings)


def test_202_ignored_warns_once(client, mock, caplog):
    ignored = {"stream_id": "technitium-dhcp", "stream_status": "ignored", "applied": False}
    mock.put(SNAPSHOT).respond(202, json=ignored)
    for _ in range(3):
        assert client.put_snapshot("technitium-dhcp", BODY) is Outcome.OK
    assert sum("ignored" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING) == 1


def test_400_logs_field_errors(client, mock, caplog):
    mock.put(SNAPSHOT).respond(400, json={"error": "Invalid request body", "details": [
        {"loc": ["items", "0", "key"], "msg": "not a MAC address", "type": "value_error"},
        {"loc": ["items", "4", "name"], "msg": "String should have at most 128 characters",
         "type": "string_too_long"}]})
    assert client.put_snapshot("technitium-dhcp", BODY) is Outcome.REJECTED
    error = [r for r in caplog.records if r.levelno == logging.ERROR][0].getMessage()
    assert "items.0.key: not a MAC address" in error
    assert "items.4.name: String should have at most 128 characters" in error


@pytest.mark.parametrize("code", [401, 403])
def test_auth_errors(client, mock, caplog, code):
    mock.put(SNAPSHOT).respond(code, json={"error": "Forbidden", "details": None})
    assert client.put_snapshot("technitium-dhcp", BODY) is Outcome.REJECTED
    assert "check STD_INGEST_KEY / key scope" in caplog.text
    assert "std_abc_secret" not in caplog.text


@pytest.mark.parametrize("code, text", [(409, "key_type"), (413, "too large")])
def test_409_and_413(client, mock, caplog, code, text):
    mock.put(SNAPSHOT).respond(code, json={"error": "Conflict", "details": None})
    assert client.put_snapshot("technitium-dhcp", BODY) is Outcome.REJECTED
    assert text in caplog.text
    assert caplog.records[-1].levelno == logging.ERROR


@pytest.mark.parametrize("response", [httpx.Response(500, text="boom"), httpx.Response(503)])
def test_5xx_is_retry(client, mock, response):
    mock.put(SNAPSHOT).mock(return_value=response)
    assert client.put_snapshot("technitium-dhcp", BODY) is Outcome.RETRY


def test_network_error_is_retry(client, mock):
    mock.put(SNAPSHOT).mock(side_effect=httpx.ConnectTimeout("timeout"))
    assert client.put_snapshot("technitium-dhcp", BODY) is Outcome.RETRY


def test_revision_change_is_logged(client, mock, caplog):
    caplog.set_level(logging.INFO)
    ok = {"stream_status": "enabled", "applied": True, "item_count": 0, "counts": {}}
    mock.put(SNAPSHOT).mock(side_effect=[
        httpx.Response(200, json=ok, headers=HEADERS),
        httpx.Response(200, json=ok, headers=HEADERS),
        httpx.Response(200, json=ok, headers={"X-STD-API-Revision": "3", "X-STD-Version": "0.11.0"}),
    ])
    for _ in range(3):
        client.put_snapshot("technitium-dhcp", BODY)
    messages = [r.getMessage() for r in caplog.records if "API revision" in r.getMessage()]
    assert messages == ["STD API revision 2 (STD 0.10.0)",
                        "STD API revision changed: 2 -> 3 (STD 0.11.0)"]
    assert client.api_revision == "3"


def test_meta_with_streams(client, mock, caplog):
    caplog.set_level(logging.INFO)
    mock.get("/api/v1/meta").respond(200, json=load_fixture("std_meta.json"), headers=HEADERS)
    assert client.check_meta() is Outcome.OK
    assert "Connected to STD 0.10.0, API revision 2" in caplog.text


def test_meta_without_streams(client, mock, caplog):
    mock.get("/api/v1/meta").respond(200, json={"std_version": "0.9.5", "api_revision": 1,
                                                "features": ["register", "management"]})
    assert client.check_meta() is Outcome.REJECTED
    assert "STD too old for streams (needs API revision ≥ 2 / STD 0.10.0)" in caplog.text


def test_meta_missing_on_old_std(client, mock, caplog):
    mock.get("/api/v1/meta").respond(404, json={"error": "Not found", "details": None})
    assert client.check_meta() is Outcome.REJECTED
    assert "STD too old for streams" in caplog.text


def test_meta_unreachable_is_retry(client, mock):
    mock.get("/api/v1/meta").mock(side_effect=httpx.ConnectError("refused"))
    assert client.check_meta() is Outcome.RETRY


def test_status_404(client, mock, caplog):
    caplog.set_level(logging.INFO)
    mock.get(STATUS).respond(404, json={"error": "Not found", "details": None})
    client.log_stream_status("technitium-dhcp")
    assert "not created yet; the first snapshot will create it as pending" in caplog.text


def test_status_200(client, mock, caplog):
    caplog.set_level(logging.INFO)
    mock.get(STATUS).respond(200, json={"stream_id": "technitium-dhcp", "stream_status": "enabled",
                                        "display_name": "DHCP", "last_snapshot_at": None,
                                        "last_applied_at": None, "last_item_count": 4,
                                        "last_counts": {}})
    client.log_stream_status("technitium-dhcp")
    assert "status=enabled" in caplog.text and "last_item_count=4" in caplog.text


def test_non_json_error_body(client, mock, caplog):
    mock.put(SNAPSHOT).respond(413, text="<html>413 Request Entity Too Large</html>")
    client.put_snapshot("technitium-dhcp", BODY)
    assert "413 Request Entity Too Large" in caplog.text
