"""Shared network analysis helpers to eliminate code duplication."""


def fix_rssi(rssi):
    """Normalize RSSI to negative dBm. Some controllers report positive values."""
    if rssi is None:
        return None
    rssi = int(rssi)
    if rssi > 0:
        rssi = -rssi
    return rssi


def is_mesh_child(device):
    """Return True if device is a wireless-uplinked (mesh) AP."""
    uplink = device.get("uplink", {}) or {}
    return uplink.get("type", "").lower() == "wireless"


def is_mesh_parent(device, all_devices):
    """Return True if any other AP uses this device as its wireless uplink."""
    device_mac = device.get("mac", "").lower()
    if not device_mac:
        return False
    for other in all_devices:
        if other.get("mac", "").lower() == device_mac:
            continue
        uplink = other.get("uplink", {}) or {}
        if uplink.get("type", "").lower() == "wireless":
            parent_mac = uplink.get("uplink_remote_mac", "").lower()
            if parent_mac == device_mac:
                return True
    return False


def get_mesh_role(device, all_devices):
    """Return mesh role string: 'mesh_child', 'mesh_parent', 'mesh_child+parent', or None."""
    child = is_mesh_child(device)
    parent = is_mesh_parent(device, all_devices)
    if child and parent:
        return "mesh_child+parent"
    if child:
        return "mesh_child"
    if parent:
        return "mesh_parent"
    return None


def ap_display_name(device):
    """Return a human-friendly AP name."""
    return device.get("name") or device.get("hostname") or device.get("mac", "unknown")
