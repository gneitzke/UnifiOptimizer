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


# --------------------------------------------------------------------------- #
# C2 -- the writer's mutation must NEVER be auto-retried on a lost response.
# The reproduction: one synthetic POST whose response is lost previously fanned
# out to FOUR dispatches (any of which could hit the live network). It must now
# dispatch exactly once and report an ambiguous, NOT-ok outcome so the caller
# keeps the before-state and reconciles via GET.
# --------------------------------------------------------------------------- #
@respx.mock
async def test_ambiguous_mutation_dispatches_once_and_is_not_ok():
    _mock_login()
    route = respx.post(f"{API}/cmd/devmgr").mock(side_effect=httpx.ReadTimeout("lost response"))
    client = UnifiClient(
        host=HOST, site=SITE, username="u", password="p", min_request_interval=0.0, max_retries=3
    )
    await client.connect()
    writer = RealControllerWriter(client)

    res = await writer.post("cmd/devmgr", {"cmd": "power-cycle", "mac": "x", "port_idx": 5})

    assert route.call_count == 1  # exactly one dispatch -- no 4x fan-out to the network
    assert res.ok is False  # never reported as success
    assert res.status_code is None
    assert res.data.get("ambiguous") is True
    await client.aclose()


# --------------------------------------------------------------------------- #
# R1 -- an explicit UniFi error envelope is a failure even on HTTP 200, and a
# non-JSON/HTML mutation response is not a confirmed success.
# --------------------------------------------------------------------------- #
@respx.mock
async def test_error_envelope_is_not_success_despite_http_200():
    _mock_login()
    respx.put(f"{API}/rest/device/dev123").mock(
        return_value=httpx.Response(
            200, json={"meta": {"rc": "error", "msg": "api.err.InvalidObject"}, "data": []}
        )
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": []})
    assert res.ok is False  # HTTP 200 must not override the error envelope
    assert res.status_code == 200
    await client.aclose()


@respx.mock
async def test_non_json_mutation_response_is_not_confirmed_success():
    _mock_login()
    respx.post(f"{API}/cmd/devmgr").mock(
        return_value=httpx.Response(200, text="<html>gateway</html>")
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.post("cmd/devmgr", {"cmd": "power-cycle"})
    assert res.ok is False  # an HTML/non-JSON body cannot confirm the mutation
    assert res.status_code == 200
    await client.aclose()


# --------------------------------------------------------------------------- #
# Verifier round 8 (#6): a non-JSON/unparseable 2xx mutation response is AMBIGUOUS
# (outcome unknown), NOT a definitive failure. A definitive failure let a revert
# report "the change was not rolled back" (a falsehood) and permitted a replay.
# --------------------------------------------------------------------------- #
@respx.mock
async def test_non_json_2xx_mutation_response_is_ambiguous_not_definitive_failure():
    _mock_login()
    respx.put(f"{API}/rest/device/dev123").mock(
        return_value=httpx.Response(200, text="<html>gateway</html>")
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": []})
    assert res.ok is False  # an HTML/non-JSON body cannot confirm the mutation
    assert res.status_code == 200
    # The 2xx means the controller ACCEPTED the request -- the write may have landed --
    # so the outcome is UNKNOWN (ambiguous), surfaced like a lost response, never a
    # definitive failure the caller can treat as "the change did not happen".
    assert isinstance(res.data, dict) and res.data.get("ambiguous") is True
    await client.aclose()


# --------------------------------------------------------------------------- #
# Verifier round 9, D6: a post-send httpx.ReadError/WriteError on a MUTATION means
# the socket faulted AFTER the request bytes went out -- the write may have landed --
# so the outcome is AMBIGUOUS (unknown), not a clean 'failed'. Before the fix these
# were absent from the client's transport-ambiguity tuple, so they escaped as an
# unhandled exception the applier recorded as a definite failure with one PUT sent.
# --------------------------------------------------------------------------- #
@respx.mock
async def test_d6_post_send_read_error_on_mutation_is_ambiguous_not_failed():
    _mock_login()
    route = respx.put(f"{API}/rest/device/dev123").mock(
        side_effect=httpx.ReadError("connection reset after send")
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": [{"radio": "ng"}]})

    assert route.call_count == 1  # dispatched exactly once -- never retried
    assert res.ok is False
    assert isinstance(res.data, dict) and res.data.get("ambiguous") is True
    await client.aclose()


@respx.mock
async def test_d6_post_send_write_error_on_mutation_is_ambiguous_not_failed():
    _mock_login()
    route = respx.put(f"{API}/rest/device/dev123").mock(
        side_effect=httpx.WriteError("broken pipe mid-send")
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": [{"radio": "ng"}]})

    assert route.call_count == 1
    assert res.ok is False
    assert isinstance(res.data, dict) and res.data.get("ambiguous") is True
    await client.aclose()


# --------------------------------------------------------------------------- #
# #w10a-1: a post-send RESPONSE-DECODING failure on a MUTATION is AMBIGUOUS, not a
# definitive failure. The controller answered HTTP 200 (the PUT was fully sent) but
# the body is corrupt gzip; httpx raises DecodingError while READING it -- a
# RequestError that is NOT a TransportError, so it escaped _RETRYABLE_EXC. Before the
# fix it leaked out of the client as an unhandled DecodingError and the applier
# recorded 'failed' despite no rejection evidence; the write may have landed.
# --------------------------------------------------------------------------- #
@respx.mock
async def test_w10a1_post_send_decode_error_on_mutation_is_ambiguous_not_failed():
    _mock_login()
    # httpx raises DecodingError while reading/decoding the (corrupt gzip) body, after
    # the PUT was fully sent -- simulated via a side_effect the transport raises.
    route = respx.put(f"{API}/rest/device/dev123").mock(
        side_effect=httpx.DecodingError("Error -3 while decompressing data")
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": [{"radio": "ng"}]})

    assert route.call_count == 1  # dispatched exactly once -- never retried
    assert res.ok is False
    # Ambiguous, NOT a clean failure: the decode failure is post-send, so the write
    # may have landed. The applier records this as "unknown", never "failed".
    assert isinstance(res.data, dict) and res.data.get("ambiguous") is True
    await client.aclose()


# --------------------------------------------------------------------------- #
# Verifier round 9, D7: an unparseable gateway 504 (and other 5xx) on a MUTATION is
# AMBIGUOUS, not a definitive rejection. A gateway timeout does NOT establish the
# write failed -- it may have landed upstream of the failing hop. Before the fix a
# non-2xx unparseable body was a definitive failure, which let a revert claim "the
# change was not rolled back" (a falsehood) and permitted a replay (a second PUT).
# --------------------------------------------------------------------------- #
@respx.mock
async def test_d7_gateway_504_unparseable_on_mutation_is_ambiguous():
    _mock_login()
    respx.put(f"{API}/rest/device/dev123").mock(
        return_value=httpx.Response(504, text="<html>gateway timeout</html>")
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": []})
    assert res.ok is False
    assert res.status_code == 504
    # The gateway timeout does not confirm a rejection -> outcome UNKNOWN, not failed.
    assert isinstance(res.data, dict) and res.data.get("ambiguous") is True
    await client.aclose()


@respx.mock
async def test_d7_5xx_unparseable_on_mutation_is_ambiguous():
    _mock_login()
    respx.put(f"{API}/rest/device/dev123").mock(
        return_value=httpx.Response(500, text="<html>error</html>")
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": []})
    assert res.ok is False
    assert res.status_code == 500
    assert isinstance(res.data, dict) and res.data.get("ambiguous") is True
    await client.aclose()


@respx.mock
async def test_d7_parseable_error_envelope_stays_definitive_rejection():
    # The narrow definitive case survives: a 504 (or any status) whose body is a
    # PARSEABLE meta.rc=error envelope is a rejection the controller confirmed --
    # a definitive failure, NOT ambiguous.
    _mock_login()
    respx.put(f"{API}/rest/device/dev123").mock(
        return_value=httpx.Response(
            502, json={"meta": {"rc": "error", "msg": "api.err.ServerBusy"}, "data": []}
        )
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": []})
    assert res.ok is False
    assert res.status_code == 502
    assert not (isinstance(res.data, dict) and res.data.get("ambiguous"))
    await client.aclose()


# --------------------------------------------------------------------------- #
# Verifier round 11 (#w11a-1): an UNPARSEABLE 4xx is AMBIGUOUS, not a definitive
# failure. Positive-proof principle: a 4xx is a confirmed rejection ONLY when its
# body is a PARSED controller rejection (meta.rc=error). A 408 timeout page, or a
# 403 carrying HTML/invalid JSON, has no parsed rejection -- so its outcome is
# UNKNOWN, exactly like an unparseable 5xx. Before the fix EVERY 4xx was a
# definitive failure, which permitted an identical replay and let a revert claim
# "the change was not rolled back" (a falsehood).
# --------------------------------------------------------------------------- #
@respx.mock
async def test_w11a1_unparseable_408_4xx_is_ambiguous_not_definitive_failure():
    _mock_login()
    respx.put(f"{API}/rest/device/dev123").mock(
        return_value=httpx.Response(408, text="<html>request timeout</html>")
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": []})
    assert res.ok is False
    assert res.status_code == 408
    # No PARSED rejection -> outcome UNKNOWN (ambiguous), never a definitive failure.
    assert isinstance(res.data, dict) and res.data.get("ambiguous") is True
    await client.aclose()


@respx.mock
async def test_w11a1_403_invalid_json_4xx_is_ambiguous_not_definitive_failure():
    _mock_login()
    respx.put(f"{API}/rest/device/dev123").mock(
        return_value=httpx.Response(403, text="}{ not json")
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": []})
    assert res.ok is False
    assert res.status_code == 403
    assert isinstance(res.data, dict) and res.data.get("ambiguous") is True
    await client.aclose()


@respx.mock
async def test_w11a1_parsed_rejection_4xx_stays_a_definitive_failure():
    # The narrow definitive case survives: a 4xx whose body IS a parsed meta.rc=error
    # envelope is a rejection the controller confirmed -- a definitive failure, NOT
    # ambiguous, so the caller may safely treat the change as not applied.
    _mock_login()
    respx.put(f"{API}/rest/device/dev123").mock(
        return_value=httpx.Response(
            400, json={"meta": {"rc": "error", "msg": "api.err.InvalidObject"}, "data": []}
        )
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": []})
    assert res.ok is False
    assert res.status_code == 400
    assert not (isinstance(res.data, dict) and res.data.get("ambiguous"))
    await client.aclose()


# --------------------------------------------------------------------------- #
# Verifier round 11 (#w11a-2): a 2xx confirms SUCCESS only with a POSITIVE success
# envelope (meta.rc=ok). An empty ``{}`` or ``meta.rc="pending"`` on 200 does NOT
# positively confirm completion, so it is AMBIGUOUS (unknown), never a confirmed
# 'applied'. Before the fix ANY dict on a 2xx was accepted as success.
# --------------------------------------------------------------------------- #
@respx.mock
async def test_w11a2_empty_dict_2xx_is_ambiguous_not_confirmed_success():
    _mock_login()
    respx.put(f"{API}/rest/device/dev123").mock(return_value=httpx.Response(200, json={}))
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": []})
    assert res.ok is False  # an empty body does not positively confirm success
    assert res.status_code == 200
    assert isinstance(res.data, dict) and res.data.get("ambiguous") is True
    await client.aclose()


@respx.mock
async def test_w11a2_rc_pending_2xx_is_ambiguous_not_confirmed_success():
    _mock_login()
    respx.put(f"{API}/rest/device/dev123").mock(
        return_value=httpx.Response(200, json={"meta": {"rc": "pending"}, "data": []})
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": []})
    assert res.ok is False  # rc != "ok" is not a positive confirmation
    assert res.status_code == 200
    assert isinstance(res.data, dict) and res.data.get("ambiguous") is True
    await client.aclose()


@respx.mock
async def test_w11a2_rc_ok_2xx_stays_confirmed_success():
    # The genuine success case survives: a 2xx carrying meta.rc=ok is a positive
    # confirmation of completion -> ok, never demoted to ambiguous.
    _mock_login()
    respx.put(f"{API}/rest/device/dev123").mock(
        return_value=httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []})
    )
    client = await _client()
    writer = RealControllerWriter(client)
    res = await writer.put("rest/device/dev123", {"radio_table": [{"radio": "ng", "channel": 6}]})
    assert res.ok is True
    assert res.status_code == 200
    assert not (isinstance(res.data, dict) and res.data.get("ambiguous"))
    await client.aclose()
