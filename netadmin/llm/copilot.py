"""The ``copilot`` investigator provider — GitHub Copilot CLI (section 10).

Shells out to a non-interactively-invoked GitHub Copilot CLI with the dossier and
captures stdout. Detection prefers the standalone ``copilot`` binary, then the
``gh copilot`` extension; the exact argv is overridable via ``NETADMIN_COPILOT_CMD``
because the CLI's flags have moved between releases and the manual provider is the
guaranteed fallback (ARCHITECTURE.md section 16).

The dossier is fed on **stdin** (it is far larger than a safe argv), and a short
instruction is passed as the prompt argument. A missing CLI raises
:class:`ProviderUnavailableError`; a non-zero exit or empty output raises
:class:`ProviderRuntimeError`. This provider only reads the local CLI's stdout —
it never touches the controller.
"""

from __future__ import annotations

import subprocess
from typing import Optional

from netadmin.llm.provider import ProviderRuntimeError, ProviderUnavailableError, _copilot_command
from netadmin.logging import get_logger

__all__ = ["CopilotProvider", "PROMPT"]

_log = get_logger("llm.copilot")

PROMPT = (
    "You are a senior network administrator. Read the network-issue dossier on "
    "stdin and answer its STRUCTURED QUESTIONS section. Respond in Markdown, "
    "beginning with a '## Answers' heading."
)

# Wall-clock ceiling for the CLI call; a wedged interactive prompt must not hang
# the daemon's request thread forever.
_TIMEOUT_S = 180.0


class CopilotProvider:
    """Runs the GitHub Copilot CLI non-interactively and returns its stdout.

    ``blocking`` is ``True`` — this spawns a subprocess and waits on network I/O,
    so the API layer runs it in a thread executor rather than on the event loop.
    """

    name = "copilot"
    blocking = True

    def __init__(self, *, prompt: str = PROMPT, timeout_s: float = _TIMEOUT_S) -> None:
        self.prompt = prompt
        self.timeout_s = timeout_s
        # Probe availability at CONSTRUCTION, not first use. build_provider() builds
        # the provider before start_investigation writes the pending investigations
        # row and emits the 'investigated' lifecycle event, so an absent CLI must
        # fail here — otherwise a box without Copilot leaves an orphan pending row, a
        # spurious 'investigated' event on the WS/HA bus, and a 502 (not 400). The
        # anthropic provider already probes in from_env(); this brings copilot level.
        if _copilot_command() is None:
            raise ProviderUnavailableError(
                "GitHub Copilot CLI not found on PATH (install `copilot` or `gh copilot`, "
                "or set NETADMIN_COPILOT_CMD)"
            )

    def _argv(self) -> list[str]:
        base = _copilot_command()
        if base is None:  # CLI removed from PATH between construction and run
            raise ProviderUnavailableError(
                "GitHub Copilot CLI not found on PATH (install `copilot` or `gh copilot`, "
                "or set NETADMIN_COPILOT_CMD)"
            )
        # `-p/--prompt` for a one-shot prompt; the dossier itself rides stdin.
        return [*base, "-p", self.prompt]

    def investigate(self, dossier: str) -> Optional[str]:
        """Invoke the CLI with the dossier on stdin; return captured stdout."""
        argv = self._argv()
        try:
            completed = subprocess.run(  # noqa: S603 - argv is built here, not user input
                argv,
                input=dossier,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except FileNotFoundError as exc:  # CLI vanished between probe and run
            raise ProviderUnavailableError(f"GitHub Copilot CLI not runnable: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderRuntimeError(
                f"GitHub Copilot CLI timed out after {self.timeout_s:.0f}s"
            ) from exc

        if completed.returncode != 0:
            # stderr may carry useful context; log it but keep the raised message tidy.
            _log.warning(
                "copilot CLI exited %d: %s", completed.returncode, completed.stderr.strip()
            )
            raise ProviderRuntimeError(
                f"GitHub Copilot CLI exited with status {completed.returncode}"
            )

        answer = (completed.stdout or "").strip()
        if not answer:
            raise ProviderRuntimeError("GitHub Copilot CLI returned no output")
        return answer
