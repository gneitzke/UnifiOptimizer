"""The read-only device-snapshot seam for the fix engine (ARCHITECTURE.md 9).

Planning a config change needs the *raw* controller device object: the planner
copies the whole ``radio_table`` (so untouched radios survive the ``rest/device``
PUT) and reads the device's Mongo ``_id`` for the endpoint, and the applier's
precondition re-check compares the live channel / tx-power / min-RSSI against what
the human reviewed. None of that is persisted in the store, so it is read live --
but **only** through a GET in the section 5.1 read set (``stat/device``), never a
mutation.

This module is the read counterpart to :mod:`netadmin.fixes.writer`. Keeping it a
narrow, injected seam is what lets a test drive the entire fix lifecycle offline:

* :class:`RealDeviceReader` -- wraps a connected :class:`UnifiClient` and pulls
  ``stat/device`` (read-only), returning the raw dict for one device MAC.
* :class:`FakeDeviceReader` -- returns hand-built snapshots and records every
  lookup, so a test asserts exactly which devices were read and that nothing else
  touched the controller.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from netadmin.logging import get_logger

__all__ = [
    "DeviceReader",
    "RealDeviceReader",
    "FakeDeviceReader",
    "device_mac_of",
]

_log = get_logger("fixes.reader")


def device_mac_of(native_id: str) -> str:
    """The device MAC underlying a radio/port entity native id.

    A radio native id is ``"<mac>:<band>"`` and a port native id is
    ``"<mac>:<idx>"`` -- one colon-separated segment beyond the six-octet MAC
    (six colons total). Strip that trailing segment; a bare device MAC (five
    colons) is returned unchanged.
    """
    if native_id.count(":") >= 6:
        return native_id.rsplit(":", 1)[0]
    return native_id


@runtime_checkable
class DeviceReader(Protocol):
    """Read one raw controller device object by MAC. Read-only, no mutation."""

    async def read_device(self, device_mac: str) -> Optional[dict[str, Any]]:
        """Return the raw ``stat/device`` object for ``device_mac``, or ``None``."""


class RealDeviceReader:
    """Pull the raw device object from the live controller (read-only).

    Wraps a :class:`~netadmin.ingest.unifi.client.UnifiClient` and fetches the
    whole ``stat/device`` list once per lookup, returning the raw dict whose
    ``mac`` matches (case-insensitive). Only ever issues a GET in the read set;
    it holds no mutation capability at all, so a fix-plan preview built on it
    cannot change the controller even in principle.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def read_device(self, device_mac: str) -> Optional[dict[str, Any]]:
        want = device_mac.lower()
        rows = await self._client.get_data("stat/device")
        for row in rows:
            if str(row.get("mac", "")).lower() == want:
                return dict(row)
        _log.info("device %s not present in stat/device", device_mac)
        return None


class FakeDeviceReader:
    """A recording, non-networked :class:`DeviceReader` for tests.

    Constructed with a ``{mac: raw_device}`` map (MACs matched case-insensitively);
    appends every requested MAC to :attr:`calls`. It opens no socket, so a test
    that injects it proves the plan/apply path read only the devices it expected
    and reached no controller.
    """

    def __init__(self, devices: Optional[Mapping[str, Mapping[str, Any]]] = None) -> None:
        self.calls: list[str] = []
        self._devices: dict[str, dict[str, Any]] = {
            k.lower(): dict(v) for k, v in (devices or {}).items()
        }

    def set_device(self, mac: str, device: Mapping[str, Any]) -> None:
        self._devices[mac.lower()] = dict(device)

    async def read_device(self, device_mac: str) -> Optional[dict[str, Any]]:
        self.calls.append(device_mac)
        found = self._devices.get(device_mac.lower())
        return dict(found) if found is not None else None
