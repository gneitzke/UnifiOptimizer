"""The ``anthropic`` investigator provider — Claude Messages API (section 10).

Calls the Claude Messages API over ``httpx`` (the project's HTTP client; the task
specifies raw HTTP, not the SDK) when ``ANTHROPIC_API_KEY`` is present in the
environment. The model is configurable via ``NETADMIN_ANTHROPIC_MODEL`` and
defaults to ``claude-sonnet-5``. No key present → the provider is simply not
built (``from_env`` raises :class:`ProviderUnavailableError`).

Security: the API key is read from the environment, sent only in the ``x-api-key``
header, and **never** logged — error paths surface the HTTP status and the API's
own error message, never request headers. This provider talks only to Anthropic;
it never touches the UniFi controller.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from netadmin.llm.provider import (
    ANTHROPIC_MODEL_ENV,
    DEFAULT_ANTHROPIC_MODEL,
    ProviderRuntimeError,
    ProviderUnavailableError,
)

__all__ = ["AnthropicProvider", "SYSTEM_PROMPT"]

_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_BASE_URL = "https://api.anthropic.com"
_DEFAULT_MAX_TOKENS = 8192
_DEFAULT_TIMEOUT_S = 180.0

SYSTEM_PROMPT = (
    "You are a senior network administrator investigating a tracked network issue. "
    "You are given a dossier compiled from a stateful monitoring system: the issue's "
    "lifecycle, evidence windows, confounders already ruled out, related issues, site "
    "topology, and the detector's playbook. Reason like an admin who remembers. Answer "
    "the dossier's STRUCTURED QUESTIONS section and nothing else. Respond in Markdown, "
    "beginning with a '## Answers' heading and using the '### ' subheadings the dossier "
    "requests, so the response can be parsed. Recommend, never apply — you cannot change "
    "the network."
)


class AnthropicProvider:
    """Sends the dossier to the Claude Messages API and returns the answer text.

    ``blocking`` is ``True`` — a synchronous network round-trip, so the API layer
    runs it in a thread executor off the event loop.
    """

    name = "anthropic"
    blocking = True

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not api_key:
            raise ProviderUnavailableError("ANTHROPIC_API_KEY is empty")
        self._api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    @classmethod
    def from_env(cls) -> "AnthropicProvider":
        """Build from the environment; raise if ``ANTHROPIC_API_KEY`` is absent."""
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderUnavailableError(
                "ANTHROPIC_API_KEY not set in the environment; the anthropic provider is unavailable"
            )
        model = os.environ.get(ANTHROPIC_MODEL_ENV, DEFAULT_ANTHROPIC_MODEL)
        base_url = os.environ.get("ANTHROPIC_BASE_URL", _DEFAULT_BASE_URL)
        return cls(api_key=api_key, model=model, base_url=base_url)

    def _headers(self) -> dict[str, str]:
        # x-api-key carries the secret; it is never logged. anthropic-version is
        # required on every Messages API request.
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _body(self, dossier: str) -> dict[str, Any]:
        # No temperature/top_p/thinking config: those are model-version-sensitive
        # (rejected on the current Sonnet/Opus tiers) and unnecessary here — a
        # single-shot investigation. model + max_tokens + system + one user turn
        # is portable across every current model.
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": dossier}],
        }

    def investigate(self, dossier: str) -> Optional[str]:
        """POST the dossier to ``/v1/messages`` and return the model's text."""
        url = f"{self.base_url}/v1/messages"
        try:
            resp = httpx.post(
                url, headers=self._headers(), json=self._body(dossier), timeout=self.timeout_s
            )
        except httpx.HTTPError as exc:
            # httpx errors carry the URL but never our headers, so the key cannot leak.
            raise ProviderRuntimeError(f"Anthropic API request failed: {exc}") from exc

        if resp.status_code != 200:
            raise ProviderRuntimeError(
                f"Anthropic API returned HTTP {resp.status_code}: {_error_message(resp)}"
            )

        data = resp.json()
        if data.get("stop_reason") == "refusal":
            raise ProviderRuntimeError("Anthropic API declined the request (safety refusal)")

        text = _extract_text(data)
        if not text:
            raise ProviderRuntimeError("Anthropic API returned no text content")
        return text


def _extract_text(data: dict[str, Any]) -> str:
    """Concatenate the ``text`` blocks of a Messages API response."""
    blocks = data.get("content")
    if not isinstance(blocks, list):
        return ""
    parts = [
        block["text"]
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text" and "text" in block
    ]
    return "".join(parts).strip()


def _error_message(resp: httpx.Response) -> str:
    """A short, key-free error string from an Anthropic error response body."""
    try:
        payload = resp.json()
    except ValueError:
        return resp.text[:200]
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or payload)
    return str(payload)[:200]
