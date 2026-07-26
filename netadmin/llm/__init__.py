"""netadmin.llm: pluggable LLM investigator (dossier builder + providers).

The deterministic detectors find and track; this package explains and correlates
(ARCHITECTURE.md section 10). :func:`~netadmin.llm.dossier.build_dossier` compiles
the provider-independent Markdown dossier; the providers (``manual`` default,
``copilot``, ``anthropic``) turn it into an answer; :mod:`~netadmin.llm.service`
orchestrates persistence and the issue trail. Nothing here mutates the controller.
"""

from netadmin.llm.dossier import build_dossier, parse_answers
from netadmin.llm.provider import (
    InvestigatorProvider,
    ProviderError,
    ProviderRuntimeError,
    ProviderUnavailableError,
    available_providers,
    build_provider,
    provider_availability,
)
from netadmin.llm.service import (
    InvestigationOutcome,
    PreparedInvestigation,
    complete_investigation,
    import_response,
    run_investigation,
    start_investigation,
)

__all__ = [
    "build_dossier",
    "parse_answers",
    "InvestigatorProvider",
    "ProviderError",
    "ProviderUnavailableError",
    "ProviderRuntimeError",
    "available_providers",
    "provider_availability",
    "build_provider",
    "InvestigationOutcome",
    "PreparedInvestigation",
    "start_investigation",
    "complete_investigation",
    "run_investigation",
    "import_response",
]
