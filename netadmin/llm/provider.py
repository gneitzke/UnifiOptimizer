"""The pluggable investigator provider seam (ARCHITECTURE.md section 10).

Deterministic detectors *find and track*; the investigator *explains and
correlates*. Every provider takes one thing — the compiled dossier
(:mod:`netadmin.llm.dossier`) — and returns either a finished answer or ``None``
to signal an answer that arrives later (the manual markdown exchange):

    class InvestigatorProvider(Protocol):
        name: str
        def investigate(self, dossier: str) -> str | None: ...

Three providers ship: ``manual`` (default, no key — writes the dossier to a file
you run through any model, response imported later), ``copilot`` (shells out to
GitHub Copilot CLI), and ``anthropic`` (Claude Messages API when a key is present).
The provider *never* mutates the controller and never auto-applies anything; it
only produces text.

This module owns the protocol, the error hierarchy, and the availability probe /
factory the CLI + API surfaces both call. The concrete providers live in
``manual.py`` / ``copilot.py`` / ``anthropic.py`` and are imported lazily so a
missing optional dependency in one never breaks the others.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

__all__ = [
    "InvestigatorProvider",
    "ProviderError",
    "ProviderUnavailableError",
    "ProviderRuntimeError",
    "ProviderAvailability",
    "PROVIDER_NAMES",
    "available_providers",
    "provider_availability",
    "build_provider",
]

# The env var the anthropic provider reads its model from (never the key — the
# key stays in ANTHROPIC_API_KEY and is never logged).
ANTHROPIC_MODEL_ENV = "NETADMIN_ANTHROPIC_MODEL"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"

PROVIDER_NAMES: tuple[str, ...] = ("manual", "copilot", "anthropic")


@runtime_checkable
class InvestigatorProvider(Protocol):
    """A pluggable LLM investigator (section 10).

    ``name`` is the stable identifier persisted in ``investigations.provider``.
    ``investigate`` returns the response markdown, or ``None`` when the answer is
    asynchronous/manual and will be attached later via import.
    """

    name: str

    def investigate(self, dossier: str) -> Optional[str]:
        """Run the investigation. ``None`` = pending (manual/async)."""


class ProviderError(Exception):
    """Base class for investigator-provider failures."""


class ProviderUnavailableError(ProviderError):
    """The requested provider is not usable in this environment.

    Raised before any work starts — e.g. ``anthropic`` with no ``ANTHROPIC_API_KEY``,
    or ``copilot`` with the CLI absent — so the caller can report it cleanly and
    fall back to the always-available ``manual`` provider.
    """


class ProviderRuntimeError(ProviderError):
    """The provider was available but failed while producing an answer."""


@dataclass(frozen=True)
class ProviderAvailability:
    """One row of the availability probe the UI provider-picker reads."""

    name: str
    available: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "available": self.available, "detail": self.detail}


def _copilot_command() -> Optional[list[str]]:
    """The detected non-interactive GitHub Copilot CLI invocation, or ``None``.

    Prefers the standalone ``copilot`` binary, then the ``gh copilot`` extension.
    Returns the argv *prefix* (the dossier is fed on stdin by the provider), or
    ``None`` when neither is on ``PATH``.
    """
    override = os.environ.get("NETADMIN_COPILOT_CMD")
    if override:
        parts = override.split()
        if parts and shutil.which(parts[0]):
            return parts
        return None
    if shutil.which("copilot"):
        return ["copilot"]
    if shutil.which("gh"):
        return ["gh", "copilot"]
    return None


def provider_availability() -> list[ProviderAvailability]:
    """Probe every provider's usability in this environment (read-only).

    ``manual`` is always available (guaranteed path, section 16). ``copilot`` needs
    the CLI on ``PATH``. ``anthropic`` needs ``ANTHROPIC_API_KEY`` in the
    environment — the key's *value* is never read into the detail string.
    """
    rows: list[ProviderAvailability] = [
        ProviderAvailability(
            name="manual",
            available=True,
            detail="Writes a dossier file to review with any model, then import the response.",
        )
    ]

    copilot_cmd = _copilot_command()
    rows.append(
        ProviderAvailability(
            name="copilot",
            available=copilot_cmd is not None,
            detail=(
                f"GitHub Copilot CLI detected ({' '.join(copilot_cmd)})."
                if copilot_cmd is not None
                else "GitHub Copilot CLI not found on PATH (install `copilot` or `gh copilot`)."
            ),
        )
    )

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    model = os.environ.get(ANTHROPIC_MODEL_ENV, DEFAULT_ANTHROPIC_MODEL)
    rows.append(
        ProviderAvailability(
            name="anthropic",
            available=has_key,
            detail=(
                f"Claude Messages API (model {model})."
                if has_key
                else "ANTHROPIC_API_KEY not set in the environment."
            ),
        )
    )
    return rows


def available_providers() -> list[dict[str, object]]:
    """Serialisable availability rows for the ``GET`` the UI picker calls."""
    return [row.as_dict() for row in provider_availability()]


def build_provider(
    name: str,
    *,
    issue_id: Optional[int] = None,
    ts: Optional[int] = None,
    base_dir: Optional[Path] = None,
) -> InvestigatorProvider:
    """Construct a provider by name, raising a clear error when it cannot run.

    ``issue_id`` / ``ts`` / ``base_dir`` are only consulted by the ``manual``
    provider (it names its dossier file ``issue-<id>-<ts>.md``). The blocking
    network providers ignore them. A bad name is a :class:`ProviderUnavailableError`
    so the caller reports one failure mode, not two.
    """
    key = name.strip().lower()
    if key == "manual":
        from netadmin.llm.manual import ManualProvider

        return ManualProvider(issue_id=issue_id, ts=ts, base_dir=base_dir)
    if key == "copilot":
        from netadmin.llm.copilot import CopilotProvider

        return CopilotProvider()
    if key == "anthropic":
        from netadmin.llm.anthropic import AnthropicProvider

        return AnthropicProvider.from_env()
    raise ProviderUnavailableError(
        f"unknown investigator provider {name!r} (expected one of {', '.join(PROVIDER_NAMES)})"
    )
