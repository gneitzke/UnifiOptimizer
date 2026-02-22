"""Authentication router — login, logout, discovery, session validation."""

import os
import sys

from fastapi import APIRouter, Header, HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from server.models.schemas import (
    AuthStatus,
    DiscoverResponse,
    LoginRequest,
    LoginResponse,
    ManualDeviceRequest,
)
from server.services.discovery import probe_single_host, scan_network
from server.services.session_manager import session_pool

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_token(authorization: str = Header(None)) -> str:
    """Extract Bearer token from Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    return authorization[7:]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/discover", response_model=DiscoverResponse)
async def discover_controllers(subnet: str = "192.168.1"):
    """Scan the local network for UniFi controllers."""
    import re
    import time

    # Validate subnet format to prevent SSRF (must be a.b.c pattern)
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}$", subnet):
        raise HTTPException(status_code=400, detail="Invalid subnet format. Expected: x.x.x")
    octets = [int(o) for o in subnet.split(".")]
    if not all(0 <= o <= 255 for o in octets):
        raise HTTPException(status_code=400, detail="Subnet octets must be 0-255")
    # Restrict to private RFC1918 ranges
    if not (
        octets[0] == 10
        or (octets[0] == 172 and 16 <= octets[1] <= 31)
        or (octets[0] == 192 and octets[1] == 168)
    ):
        raise HTTPException(status_code=400, detail="Only private network subnets allowed")

    start = time.time()
    devices = await scan_network(subnet=subnet, timeout=0.8)
    elapsed_ms = int((time.time() - start) * 1000)
    return DiscoverResponse(devices=devices, scan_duration_ms=elapsed_ms)


@router.post("/manual")
async def manual_device(req: ManualDeviceRequest):
    """Verify a manually entered controller host is reachable."""
    device = await probe_single_host(req.host)
    if device is None:
        raise HTTPException(status_code=404, detail="No UniFi controller found at that address")
    return device


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """Authenticate to a UniFi controller and return a JWT session token."""
    from api.cloudkey_gen2_client import CloudKeyGen2Client

    try:
        client = CloudKeyGen2Client(req.host, req.username, req.password, req.site)
        if not client.login():
            raise HTTPException(status_code=401, detail="Invalid credentials or controller error")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Cannot reach controller: {str(e)}")

    token = session_pool.create_session(client, req.host, req.username, req.password, req.site)

    from server.services.session_manager import JWT_EXPIRY_DAYS

    return LoginResponse(
        token=token,
        expires_in=JWT_EXPIRY_DAYS * 86400,
        controller_type="cloudkey_gen2",
        site=req.site,
        host=req.host,
        username=req.username,
    )


@router.post("/validate")
async def validate_session(authorization: str = Header(None)):
    """Check if an existing JWT token is still valid and the controller session is alive."""
    token = _get_token(authorization)
    entry = session_pool.get_session(token)
    if entry is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    # Quick API probe to confirm controller session is alive
    try:
        entry.client.get(f"s/{entry.site}/self")
    except Exception:
        # Try re-authentication transparently
        if not session_pool.re_authenticate(token):
            raise HTTPException(
                status_code=401,
                detail="Controller session expired and re-authentication failed",
            )

    return {"valid": True, "host": entry.host, "site": entry.site}


@router.post("/logout")
async def logout(authorization: str = Header(None)):
    """Log out and destroy the server-side session."""
    token = _get_token(authorization)
    session_pool.remove_session(token)
    return {"logged_out": True}


@router.get("/status", response_model=AuthStatus)
async def auth_status(authorization: str = Header(None)):
    """Return the current authentication status."""
    if not authorization or not authorization.startswith("Bearer "):
        return AuthStatus(authenticated=False)

    token = authorization[7:]
    payload = session_pool.validate_token(token)
    if payload is None:
        return AuthStatus(authenticated=False)

    # Also verify that a server-side session exists (survives restart check)
    entry = session_pool.get_session(token)
    if entry is None:
        # JWT is valid but no session — try re-authenticating from stored creds
        if session_pool.re_authenticate(token):
            entry = session_pool.get_session(token)

    if entry is None:
        return AuthStatus(authenticated=False)

    return AuthStatus(
        authenticated=True,
        host=payload.get("host"),
        username=payload.get("username"),
        site=payload.get("site"),
        expires_at=str(payload.get("exp")),
    )
