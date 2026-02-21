"""Change tracker — tracks applied changes with full revert capability.

Snapshots device configuration before each change so it can be reverted.
Persists history to data/change_history.json.
"""

import json
import os
import uuid
from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional


_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "change_history.json",
)


class ChangeTracker:
    """Tracks changes with before/after snapshots for revert support."""

    def __init__(self, history_path: str = _HISTORY_PATH):
        self._path = history_path
        self._lock = Lock()
        self._history: List[Dict[str, Any]] = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> list:
        try:
            with open(self._path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._history, f, indent=2, default=str)

    # ------------------------------------------------------------------
    # Record
    # ------------------------------------------------------------------

    def record_change(
        self,
        device_name: str,
        device_mac: str,
        action: str,
        before_config: Dict[str, Any],
        after_config: Dict[str, Any],
        status: str = "applied",
    ) -> str:
        """Record a change and return its unique change_id."""
        change_id = str(uuid.uuid4())[:8]
        entry = {
            "change_id": change_id,
            "device_name": device_name,
            "device_mac": device_mac,
            "action": action,
            "before_config": before_config,
            "after_config": after_config,
            "status": status,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "reverted": False,
            "reverted_at": None,
        }
        with self._lock:
            self._history.append(entry)
            self._save()
        return change_id

    # ------------------------------------------------------------------
    # Revert
    # ------------------------------------------------------------------

    def mark_reverted(self, change_id: str) -> bool:
        """Mark a change as reverted.  Returns True if found."""
        with self._lock:
            for entry in self._history:
                if entry["change_id"] == change_id:
                    entry["reverted"] = True
                    entry["reverted_at"] = datetime.utcnow().isoformat() + "Z"
                    self._save()
                    return True
        return False

    def get_change(self, change_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for entry in self._history:
                if entry["change_id"] == change_id:
                    return entry.copy()
        return None

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return recent change history (newest first)."""
        with self._lock:
            return list(reversed(self._history[-limit:]))

    def get_revertable(self) -> List[Dict[str, Any]]:
        """Return changes that can still be reverted."""
        with self._lock:
            return [
                e.copy()
                for e in self._history
                if e["status"] == "applied" and not e["reverted"]
            ]


# Singleton
change_tracker = ChangeTracker()
