"""ControllerWriter seam: the fake records + cans, the real one wraps the client.

The RealControllerWriter is the only object allowed to send a mutating call, so it
is tested against a fully mocked HTTP layer (``respx``) -- never a real controller.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from netadmin.fixes.models import WriteResult
from netadmin.fixes.writer import ControllerWriter, FakeControllerWriter, RealControllerWriter
from netadmin.ingest.unifi.client import UnifiClient

pytestmark = pytest.mark.asyncio

HOST = "https://ctrl.test"
SITE = "default"
OS_PROBE = f"{HOST}/proxy/network/"
OS_LOGIN = f"{HOST}/api/auth/login"
API = f"{HOST}/proxy/network/api/s/{SITE}"


# --------------------------------------------------------------------------- #
# Fake
# --------------------------------------------------------------------------- #
async def test_fake_records_calls_and_returns_canned_response():
    writer = FakeControllerWriter()
    assert isinstance(writer, ControllerWriter)  # structural conformance

    put = await writer.put("rest/device/abc", {"radio_table": [{"radio": "ng"}]})
    post = await writer.post("cmd/devmgr", {"cmd": "power-cycle"})

    assert put.ok and post.ok
    assert writer.call_count == 2
    assert writer.calls[0].method == "PUT"
    assert writer.calls[0].endpoint == "rest/device/abc"
    assert writer.calls[0].body == {"radio_table": [{"radio": "ng"}]}
    assert writer.calls[1].method == "POST"


async def test_fake_fail_on_returns_non_ok_but_still_records():
    writer = FakeControllerWriter(fail_on={"PUT rest/device/abc"})
    res = await writer.put("rest/device/abc", {})
    assert res.ok is False
    assert res.status_code == 500
    assert writer.call_count == 1  # a failed call is still an observed call


async def test_fake_raise_on_simulates_transport_error():
    writer = FakeControllerWriter(raise_on={"POST cmd/devmgr"})
    with pytest.raises(RuntimeError):
        await writer.post("cmd/devmgr", {})
    assert writer.call_count == 1


async def test_fake_custom_response():
    writer = FakeControllerWriter(response=WriteResult(ok=True, status_code=201, data={"x": 1}))
    res = await writer.put("rest/device/abc", {})
    assert res.status_code == 201 and res.data == {"x": 1}


# --------------------------------------------------------------------------- #
# Real (mocked HTTP)
# --------------------------------------------------------------------------- #
def _mock_login() -> None:
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(200, headers={"X-CSRF-Token": "c"}, json={})
    )


async def _client() -> UnifiClient:
    client = UnifiClient(host=HOST, site=SITE, username="u", password="p", min_request_interval=0.0)
    await client.connect()
    return client


@respx.mock
async def test_real_writer_sends_put_and_parses_ok():
    _mock_login()
    route = respx.put(f"{API}/rest/device/dev123").mock(
        return_value=httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []})
    )
    client = await _client()
    writer = RealControllerWriter(client)
    body = {"radio_table": [{"radio": "ng", "channel": 6}]}
    res = await writer.put("rest/device/dev123", body)

    assert route.called
    sent = route.calls.last.request
    assert sent.method == "PUT"
    import json as _json

    assert _json.loads(sent.content) == body
    assert res.ok and res.status_code == 200
    await client.aclose()


@respx.mock
async def test_real_writer_sends_post_command():
    _mock_login()
    route = respx.post(f"{API}/cmd/devmgr").mock(
        return_value=httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []})
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.post("cmd/devmgr", {"cmd": "power-cycle", "mac": "x", "port_idx": 5})
    assert route.called and res.ok
    await client.aclose()


@respx.mock
async def test_real_writer_reports_non_2xx_as_not_ok():
    _mock_login()
    respx.put(f"{API}/rest/device/dev123").mock(return_value=httpx.Response(400, json={}))
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": []})
    assert res.ok is False
    assert res.status_code == 400
    await client.aclose()
