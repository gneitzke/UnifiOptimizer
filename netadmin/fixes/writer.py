"""The single controller-mutation seam (``docs/ARCHITECTURE.md`` section 9).

Every PUT/POST that could change the live controller passes through a
:class:`ControllerWriter`. There are exactly two implementations:

* :class:`RealControllerWriter` -- the *only* new code in the whole rebuild that
  issues a mutating controller call. A thin wrapper over the existing async UniFi
  client's request path; it holds no policy (no dry-run, no confirmation, no
  precondition logic -- those live in the applier). If it is constructed, a real
  mutation is intended.
* :class:`FakeControllerWriter` -- records every call and returns canned responses,
  so the entire applier can be exercised (dry-run *and* confirmed apply) without a
  socket ever opening. Every fix-engine test injects this.

Keeping the seam this narrow is what lets the test-suite prove, structurally, that
no dry-run path can reach the network: a dry-run never touches a writer at all, and
the confirmed-apply tests only ever inject the fake.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from netadmin.fixes.models import WriteResult
from netadmin.ingest.unifi.auth import UnifiAmbiguousOutcomeError
from netadmin.ingest.unifi.client import envelope_error
from netadmin.logging import get_logger

__all__ = [
    "ControllerWriter",
    "RealControllerWriter",
    "FakeControllerWriter",
    "RecordedCall",
]

_log = get_logger("fixes.writer")


@runtime_checkable
class ControllerWriter(Protocol):
    """The mutation interface the applier depends on -- nothing wider.

    Both methods take a **site-relative** endpoint (``rest/device/<id>``,
    ``cmd/devmgr``) and a JSON body, and return a :class:`WriteResult`. They are the
    only two verbs the fix engine ever needs; the applier chooses between them by a
    step's ``method``.
    """

    async def put(self, endpoint: str, body: Mapping[str, Any]) -> WriteResult:
        """PUT ``body`` to a site-relative ``endpoint`` (e.g. ``rest/device/<id>``)."""

    async def post(self, endpoint: str, body: Mapping[str, Any]) -> WriteResult:
        """POST ``body`` to a site-relative ``endpoint`` (e.g. ``cmd/devmgr``)."""


class RecordedCall:
    """One (method, endpoint, body) a :class:`FakeControllerWriter` observed."""

    __slots__ = ("method", "endpoint", "body")

    def __init__(self, method: str, endpoint: str, body: Mapping[str, Any]) -> None:
        self.method = method
        self.endpoint = endpoint
        self.body = dict(body)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"RecordedCall({self.method} {self.endpoint} {self.body!r})"


class RealControllerWriter:
    """Wrap the live UniFi client so the applier can mutate exactly one way.

    This is the sole production object permitted to send a mutating call
    (section 9). It performs no dry-run, confirmation, or precondition checks --
    those are the applier's job and run *before* control ever reaches here. It only
    translates ``put``/``post`` into the client's authenticated request and reports
    a :class:`WriteResult`. Constructing it signals intent to actually change the
    controller, which the applier's dry-run path deliberately never does.
    """

    def __init__(self, client: Any) -> None:
        # ``client`` is a netadmin.ingest.unifi.client.UnifiClient. Typed as Any to
        # keep this module import-light and the seam swappable in tests.
        self._client = client

    async def put(self, endpoint: str, body: Mapping[str, Any]) -> WriteResult:
        return await self._send("PUT", endpoint, body)

    async def post(self, endpoint: str, body: Mapping[str, Any]) -> WriteResult:
        return await self._send("POST", endpoint, body)

    async def _send(self, method: str, endpoint: str, body: Mapping[str, Any]) -> WriteResult:
        _log.info("controller mutation: %s %s", method, endpoint)
        # allow_mutation=True: this is the single sanctioned controller-mutation
        # seam, so it is the only caller permitted past the client's GET-only gate
        # (S1). The client will not auto-retry this on an ambiguous transport
        # failure (C2) -- it raises instead, and we surface the ambiguity below.
        try:
            resp = await self._client.request(
                method, endpoint, json_body=dict(body), allow_mutation=True
            )
        except UnifiAmbiguousOutcomeError as exc:
            # Lost response: the mutation may or may not have landed. Never report
            # success and never retry -- the caller keeps the before-state and
            # reconciles the live state via a GET read before any further attempt.
            _log.warning("ambiguous mutation outcome: %s %s: %s", method, endpoint, exc)
            return WriteResult(
                ok=False, status_code=None, data={"ambiguous": True, "error": str(exc)}
            )

        status = getattr(resp, "status_code", None)
        data: Any = None
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001 - a non-JSON body cannot confirm success
            data = None

        status_int = int(status) if status is not None else None
        http_ok = status_int is not None and 200 <= status_int < 300
        is_client_error = status_int is not None and 400 <= status_int < 500
        confirmed_json = isinstance(data, dict)
        # A parseable classic envelope that reports ``meta.rc="error"`` is a rejection
        # the controller CONFIRMED, at any HTTP status (R1): a 200 carrying that body
        # failed just as surely as a 400 carrying it.
        envelope_err = envelope_error(data) if confirmed_json else None

        # --- The unified mutation-outcome classification (D6/D7). -----------------
        # A mutation outcome is DEFINITIVELY REJECTED only when we have positive proof
        # it did not land: a parseable ``meta.rc=error`` envelope, or a 4xx client error
        # (the controller received and refused the request before applying it). These
        # are the ONLY definitive failures; the caller may retry/replay them safely.
        if envelope_err is not None or is_client_error:
            return WriteResult(ok=False, status_code=status, data=data)

        # CONFIRMED SUCCESS: a 2xx with a parseable envelope that reports no error.
        if http_ok and confirmed_json:
            return WriteResult(ok=True, status_code=status, data=data)

        # Everything else is AMBIGUOUS (outcome UNKNOWN), never a definitive failure:
        #   * a 2xx whose body we could NOT parse (an HTML/proxy page) -- the controller
        #     ACCEPTED the request (2xx) but the body cannot confirm the outcome (#6);
        #   * a gateway 504 / other 5xx / any non-2xx whose body does not CONFIRM a
        #     rejection (D7) -- a gateway timeout does NOT establish the write failed;
        #     the mutation may well have landed upstream of the failing hop.
        # Surface it exactly like a lost response -- ``ok=False`` with
        # ``data={"ambiguous": True, ...}`` -- so the applier records it as unknown (not
        # "reverted"/"failed"), the API never claims "the change was not rolled back",
        # and no automatic replay is allowed.
        _log.warning(
            "ambiguous mutation response: %s %s (status=%s); outcome unknown -- "
            "not a definitive rejection",
            method,
            endpoint,
            status,
        )
        return WriteResult(
            ok=False,
            status_code=status,
            data={
                "ambiguous": True,
                "error": (
                    f"controller returned an unconfirmable response (status={status}); "
                    "the body does not confirm a rejection, so the mutation outcome is "
                    "unknown -- reconcile via a read, do not assume it did not land"
                ),
            },
        )


class FakeControllerWriter:
    """A recording, non-networked :class:`ControllerWriter` for tests.

    Appends every call to :attr:`calls` and returns a canned :class:`WriteResult`.
    Configure failures with ``fail_on`` (a set of ``"METHOD endpoint"`` keys that
    return ``ok=False``) or ``raise_on`` (keys that raise, simulating a transport
    error). Defaults to succeeding. It never opens a socket, so any test that
    injects it -- and asserts ``calls == []`` -- proves a code path sent nothing.
    """

    def __init__(
        self,
        *,
        response: Optional[WriteResult] = None,
        fail_on: Optional[set[str]] = None,
        raise_on: Optional[set[str]] = None,
    ) -> None:
        self.calls: list[RecordedCall] = []
        self._response = response or WriteResult(ok=True, status_code=200, data={"meta": {}})
        self._fail_on = fail_on or set()
        self._raise_on = raise_on or set()

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def put(self, endpoint: str, body: Mapping[str, Any]) -> WriteResult:
        return await self._record("PUT", endpoint, body)

    async def post(self, endpoint: str, body: Mapping[str, Any]) -> WriteResult:
        return await self._record("POST", endpoint, body)

    async def _record(self, method: str, endpoint: str, body: Mapping[str, Any]) -> WriteResult:
        self.calls.append(RecordedCall(method, endpoint, body))
        key = f"{method} {endpoint}"
        if key in self._raise_on:
            raise RuntimeError(f"simulated transport failure for {key}")
        if key in self._fail_on:
            return WriteResult(ok=False, status_code=500, data={"error": "simulated"})
        return self._response
