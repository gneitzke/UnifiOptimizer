"""Session manager — maintains a pool of authenticated UniFi controller sessions.

Maps JWT tokens to active CloudKeyGen2Client instances with automatic
re-authentication when sessions expire.
"""

import os
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from jose import jwt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# JWT configuration
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    import secrets

    JWT_SECRET = secrets.token_hex(32)
    print(
        "WARNING: JWT_SECRET not set — using random secret. "
        "Sessions will not survive restarts. Set JWT_SECRET env var in production."
    )
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 90


class SessionEntry:
    """A single authenticated controller session."""

    __slots__ = ("client", "host", "username", "password", "site", "created_at", "last_used")

    def __init__(self, client, host, username, password, site):
        self.client = client
        self.host = host
        self.username = username
        self.password = password
        self.site = site
        self.created_at = datetime.utcnow()
        self.last_used = datetime.utcnow()

    def touch(self):
        self.last_used = datetime.utcnow()


class SessionPool:
    """Thread-safe pool of controller sessions keyed by JWT jti."""

    def __init__(self):
        self._sessions: Dict[str, SessionEntry] = {}
        self._lock = threading.Lock()

    def create_session(self, client, host, username, password, site) -> str:
        """Store a new authenticated session and return a JWT token."""
        import uuid

        jti = str(uuid.uuid4())
        entry = SessionEntry(client, host, username, password, site)

        with self._lock:
            self._sessions[jti] = entry

        payload = {
            "jti": jti,
            "host": host,
            "username": username,
            "site": site,
            "iat": datetime.utcnow(),
            "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRY_DAYS),
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return token

    def get_session(self, token: str) -> Optional[SessionEntry]:
        """Validate a JWT token and return the session, or None."""
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except Exception:
            return None

        jti = payload.get("jti")
        if not jti:
            return None

        with self._lock:
            entry = self._sessions.get(jti)

        if entry:
            entry.touch()
        return entry

    def remove_session(self, token: str) -> bool:
        """Remove (logout) a session by token."""
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except Exception:
            return False

        jti = payload.get("jti")
        with self._lock:
            return self._sessions.pop(jti, None) is not None

    def validate_token(self, token: str) -> Optional[dict]:
        """Decode and validate a JWT, returning the payload or None."""
        try:
            return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except Exception:
            return None

    def re_authenticate(self, token: str) -> bool:
        """Re-authenticate a session using stored credentials.

        Returns True if re-auth succeeded.
        """
        entry = self.get_session(token)
        if not entry:
            return False

        from api.cloudkey_gen2_client import CloudKeyGen2Client

        try:
            new_client = CloudKeyGen2Client(
                entry.host, entry.username, entry.password, entry.site
            )
            if new_client.login():
                entry.client = new_client
                return True
        except Exception:
            pass
        return False

    def clear_all(self):
        """Remove all sessions (used on shutdown)."""
        with self._lock:
            self._sessions.clear()


# Singleton instance
session_pool = SessionPool()
