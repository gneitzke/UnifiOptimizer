"""The default ``manual`` investigator provider (ARCHITECTURE.md section 10).

No API key, no network, no dependency on an external CLI — the guaranteed path
(section 16). :meth:`ManualProvider.investigate` writes the compiled dossier to
``investigations/issue-<id>-<ts>.md`` and returns ``None`` (the answer is
*pending*): you run that file through whatever model you like, then attaches
the response with ``netadmin investigate import`` or the paste box in the UI.

The output directory is gitignored — a dossier embeds live network topology and
should never enter a public repo (see the repo's PUBLIC RULE). The provider only
writes local files; it never touches the controller.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

__all__ = ["ManualProvider", "default_base_dir"]


def default_base_dir() -> Path:
    """The gitignored ``investigations/`` directory under the project root.

    Imported lazily from :mod:`netadmin.config` so importing this module never
    forces settings construction.
    """
    from netadmin.config import PROJECT_ROOT

    return PROJECT_ROOT / "investigations"


class ManualProvider:
    """Writes the dossier to a local file; the response is imported later.

    ``blocking`` is ``False`` — the only work is a local file write, so the
    service layer runs it inline on the event loop rather than deferring it to a
    thread. ``investigate`` always returns ``None`` (pending); the written path is
    exposed on :attr:`output_path` so the caller can tell the user where to look.
    """

    name = "manual"
    blocking = False

    def __init__(
        self,
        *,
        issue_id: Optional[int] = None,
        ts: Optional[int] = None,
        base_dir: Optional[Path] = None,
    ) -> None:
        self.issue_id = issue_id
        self.ts = ts
        self.base_dir = Path(base_dir) if base_dir is not None else default_base_dir()
        self.output_path: Optional[Path] = None

    def _filename(self) -> str:
        issue = "unknown" if self.issue_id is None else str(int(self.issue_id))
        ts = "0" if self.ts is None else str(int(self.ts))
        return f"issue-{issue}-{ts}.md"

    def investigate(self, dossier: str) -> None:
        """Write the dossier to ``investigations/issue-<id>-<ts>.md``; return None.

        The directory is created if absent (gitignored). Returning ``None`` marks
        the investigation *pending* — no answer yet.
        """
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.base_dir / self._filename()
        path.write_text(dossier, encoding="utf-8")
        self.output_path = path
        return None
