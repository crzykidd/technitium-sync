import pytest

from technitium_sync.mapping import MAX_ITEMS, build_items, normalize_mac
from technitium_sync.technitium import Reservation


@pytest.mark.parametrize("value", [
    "aa:bb:cc:dd:ee:ff",
    "AA:BB:CC:DD:EE:FF",
    "aa-bb-cc-dd-ee-ff",
    "AA-BB-CC-DD-EE-FF",
    "aabb.ccdd.eeff",
    "AABB.CCDD.EEFF",
    "aabbccddeeff",
    "AABBCCDDEEFF",
    "  AA-BB-CC-DD-EE-FF  ",
])
def test_normalize_mac_accepts_common_forms(value):
    assert normalize_mac(value) == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize("value", [
    "",
    None,
    "aa:bb:cc:dd:ee",           # too short
    "aa:bb:cc:dd:ee:ff:00",     # too long
    "aa:bb-cc:dd:ee:ff",        # mixed separators
    "gg:bb:cc:dd:ee:ff",        # not hex
    "aabb.ccdd.eef",            # dotted, short
    "aabbccddeef",              # bare, 11 digits
    "a:b:c:d:e:f",              # single digits
])
def test_normalize_mac_rejects(value):
    with pytest.raises(ValueError):
        normalize_mac(value)


def res(mac="02-00-00-00-00-01", name="host", scope="LAN", address="192.168.1.10", comments=None):
    return Reservation(scope=scope, host_name=name, hardware_address=mac, address=address,
                       comments=comments)


def test_basic_item_shape():
    result = build_items([res(comments="Rack 2")])
    assert result.items == [{
        "key": "02:00:00:00:00:01",
        "mac": "02:00:00:00:00:01",
        "name": "host",
        "network": "LAN",
        "ip": "192.168.1.10",
        "attributes": {"scope": "LAN", "comment": "Rack 2"},
    }]
    assert result.skipped == 0


@pytest.mark.parametrize("name", [None, "", "   "])
def test_missing_hostname_is_skipped_and_counted(name, caplog):
    result = build_items([res(name=name), res(mac="02-00-00-00-00-02", name="ok")])
    assert [i["name"] for i in result.items] == ["ok"]
    assert result.skipped_no_name == 1
    assert "no hostname" in caplog.text


@pytest.mark.parametrize("comments", [None, "", "   "])
def test_empty_comment_is_omitted(comments):
    item = build_items([res(comments=comments)]).items[0]
    assert item["attributes"] == {"scope": "LAN"}


def test_long_comment_is_truncated():
    result = build_items([res(comments="x" * 300)])
    assert result.items[0]["attributes"]["comment"] == "x" * 256
    assert result.truncated == 1
    assert result.skipped == 0


def test_long_name_is_skipped():
    result = build_items([res(name="n" * 129), res(mac="02-00-00-00-00-02", name="n" * 128)])
    assert [len(i["name"]) for i in result.items] == [128]
    assert result.skipped_too_long == 1


def test_long_network_is_skipped():
    result = build_items([res(scope="s" * 65), res(mac="02-00-00-00-00-02", scope="s" * 64)])
    assert [len(i["network"]) for i in result.items] == [64]
    assert result.skipped_too_long == 1


def test_duplicate_mac_across_scopes_keeps_first(caplog):
    result = build_items([
        res(mac="02-00-00-00-00-01", name="first", scope="LAN"),
        res(mac="02:00:00:00:00:01", name="second", scope="IoT"),
    ])
    assert [(i["name"], i["network"]) for i in result.items] == [("first", "LAN")]
    assert result.duplicates == 1
    assert "Duplicate MAC 02:00:00:00:00:01" in caplog.text


def test_bad_mac_is_skipped():
    result = build_items([res(mac="not-a-mac")])
    assert result.items == []
    assert result.skipped_bad_mac == 1


def test_bad_or_missing_address_omits_ip():
    result = build_items([res(address=None), res(mac="02-00-00-00-00-02", address="999.1.1.1")])
    assert all("ip" not in i for i in result.items)
    assert len(result.items) == 2


def test_item_count_over_limit_is_reported_not_trimmed():
    many = [res(mac=f"{n:012x}", name=f"h{n}") for n in range(MAX_ITEMS + 1)]
    result = build_items(many)
    # build_items never trims: the loop refuses to send (see test_main).
    assert len(result.items) == MAX_ITEMS + 1
