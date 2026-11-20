"""Turn Technitium reservations into STD snapshot items, within the PRD §18.5 limits."""

import ipaddress
import logging
import re
from dataclasses import dataclass, field

from technitium_sync.technitium import Reservation

log = logging.getLogger(__name__)

# PRD §18.5 limits.
MAX_ITEMS = 5000
MAX_NAME = 128
MAX_NETWORK = 64
MAX_ATTRIBUTE_STRING = 256
MAX_BODY_BYTES = 2 * 1024 * 1024

_HEX = "[0-9a-f]"
_MAC_FORMS = (
    re.compile(rf"^{_HEX}{{2}}(:{_HEX}{{2}}){{5}}$"),  # aa:bb:cc:dd:ee:ff
    re.compile(rf"^{_HEX}{{2}}(-{_HEX}{{2}}){{5}}$"),  # aa-bb-cc-dd-ee-ff
    re.compile(rf"^{_HEX}{{4}}(\.{_HEX}{{4}}){{2}}$"),  # aabb.ccdd.eeff
    re.compile(rf"^{_HEX}{{12}}$"),  # aabbccddeeff
)


def normalize_mac(value: str) -> str:
    """Any MAC form §18.5 accepts, as lower-case colon form. Raises `ValueError` otherwise."""
    text = (value or "").strip().lower()
    if not any(form.match(text) for form in _MAC_FORMS):
        raise ValueError(f"not a MAC address: {value!r}")
    digits = re.sub(r"[^0-9a-f]", "", text)
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def _truncate(value: str) -> str:
    return value if len(value) <= MAX_ATTRIBUTE_STRING else value[:MAX_ATTRIBUTE_STRING]


@dataclass
class MappingResult:
    items: list[dict] = field(default_factory=list)
    skipped_no_name: int = 0
    skipped_bad_mac: int = 0
    skipped_too_long: int = 0
    duplicates: int = 0
    truncated: int = 0

    @property
    def skipped(self) -> int:
        return self.skipped_no_name + self.skipped_bad_mac + self.skipped_too_long + self.duplicates


def build_items(reservations: list[Reservation]) -> MappingResult:
    """Map reservations to items. Bad ones are skipped (and counted), never sent."""
    result = MappingResult()
    seen: dict[str, str] = {}  # mac -> scope of the first reservation that used it

    for res in reservations:
        try:
            mac = normalize_mac(res.hardware_address)
        except ValueError:
            log.warning("Skipping reservation in scope %r: bad hardware address %r",
                        res.scope, res.hardware_address)
            result.skipped_bad_mac += 1
            continue

        name = (res.host_name or "").strip()
        if not name:
            log.warning("Skipping reservation %s in scope %r: no hostname", mac, res.scope)
            result.skipped_no_name += 1
            continue
        if len(name) > MAX_NAME:
            log.warning("Skipping reservation %s in scope %r: hostname longer than %d chars",
                        mac, res.scope, MAX_NAME)
            result.skipped_too_long += 1
            continue
        if len(res.scope) > MAX_NETWORK:
            log.warning("Skipping reservation %s: scope name %r longer than %d chars",
                        mac, res.scope, MAX_NETWORK)
            result.skipped_too_long += 1
            continue

        if mac in seen:
            log.warning("Duplicate MAC %s in scope %r (already in scope %r): keeping the first",
                        mac, res.scope, seen[mac])
            result.duplicates += 1
            continue
        seen[mac] = res.scope

        item = {"key": mac, "mac": mac, "name": name, "network": res.scope}

        address = (res.address or "").strip()
        if address:
            try:
                item["ip"] = str(ipaddress.ip_address(address))
            except ValueError:
                log.warning("Reservation %s (%s): ignoring bad address %r", mac, name, address)

        attributes = {"scope": _truncate(res.scope)}
        comment = (res.comments or "").strip()
        if comment:
            if len(comment) > MAX_ATTRIBUTE_STRING:
                result.truncated += 1
                log.info("Reservation %s (%s): comment truncated to %d chars",
                         mac, name, MAX_ATTRIBUTE_STRING)
            attributes["comment"] = _truncate(comment)
        item["attributes"] = attributes

        result.items.append(item)

    return result
