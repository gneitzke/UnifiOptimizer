"""AI-powered network advisor — pluggable backends (Ollama, Claude, OpenAI).

Usage:
    from core.ai_advisor import ask, generate_summary

    answer = ask("Why does my iPad keep dropping?")
    summary = generate_summary(analysis_data, recommendations)

Backends (configured via data/config.yaml → ai.backend):
    ollama  — local, free, no account required (default)
    claude  — Anthropic API (ANTHROPIC_API_KEY env var)
    openai  — OpenAI API   (OPENAI_API_KEY env var)
"""

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Context management
# ---------------------------------------------------------------------------


def _load_latest_cache():
    """Find and load the most recent analysis_cache_*.json from the working directory."""
    cache_files = sorted(
        Path(".").glob("analysis_cache_*.json"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )
    if not cache_files:
        return None, None
    with open(cache_files[0]) as f:
        data = json.load(f)
    return data.get("full_analysis"), data.get("recommendations", [])


def _build_context(analysis_data, recommendations):
    """Build a compact human-readable summary from analysis data.

    Raw JSON dumps overwhelm small models. This extracts only the fields that
    matter and formats them as plain text so any model can reason over them.
    """
    lines = []

    # Health score
    hs = analysis_data.get("health_score", {})
    score = hs.get("score", "?")
    grade = hs.get("grade", "?")
    status = hs.get("status", "")
    lines.append(f"NETWORK HEALTH: {score}/100 (Grade {grade} — {status})")

    ha = analysis_data.get("health_analysis", {})
    cs = ha.get("component_scores", {})
    if cs:
        lines.append("Component scores: " + ", ".join(f"{k}: {v}" for k, v in cs.items()))
    lines.append("")

    # Access points
    ap_analysis = analysis_data.get("ap_analysis", {})
    ap_details = ap_analysis.get("ap_details", [])
    if ap_details:
        lines.append(f"ACCESS POINTS ({len(ap_details)} total):")
        for ap in ap_details:
            mesh_tag = " [MESH]" if ap.get("is_mesh") else ""
            n_clients = ap.get("client_count", 0)
            radios = ap.get("radios", {})
            radio_parts = []
            for band, r in radios.items():
                ch = r.get("channel", "auto")
                width = r.get("width", "")
                radio_parts.append(f"{band} ch{ch}/{width}MHz")
            lines.append(
                f"  {ap['name']}{mesh_tag} ({ap.get('model', '')}) — "
                f"{n_clients} clients — {', '.join(radio_parts)}"
            )
    lines.append("")

    # Client findings
    client_findings = analysis_data.get("client_findings", [])
    if client_findings:
        lines.append("CLIENT ISSUES:")
        for cf in client_findings:
            lines.append(
                f"  [{cf.get('severity', '').upper()}] {cf.get('message', '')} "
                f"(RSSI: {cf.get('rssi', '?')} dBm, AP: {cf.get('ap', '?')})"
            )
        lines.append("")

    # Network-level issues
    issues = ha.get("issues", [])
    if issues:
        lines.append("NETWORK ISSUES:")
        for issue in issues:
            lines.append(f"  [{issue.get('severity', '').upper()}] {issue.get('message', '')}")
            if issue.get("recommendation"):
                lines.append(f"    Recommended fix: {issue['recommendation']}")
        lines.append("")

    # Recommendations — strip the full device blob, keep only what matters
    typed_recs = analysis_data.get("recommendations", []) or recommendations
    if typed_recs:
        lines.append("RECOMMENDATIONS:")
        for i, rec in enumerate(typed_recs, 1):
            device = rec.get("device", {})
            device_name = device.get("name", "Unknown") if isinstance(device, dict) else str(device)
            action = rec.get("action", rec.get("type", ""))
            reason = rec.get("reason", rec.get("message", rec.get("recommendation", "")))
            priority = rec.get("priority", "")
            band = rec.get("band", "")
            band_str = f" [{band}]" if band else ""
            lines.append(f"  {i}. [{priority.upper()}]{band_str} {device_name}: {action}")
            if reason:
                lines.append(f"     {reason}")
        lines.append("")

    # DFS radar events
    dfs = analysis_data.get("dfs_analysis", {})
    if isinstance(dfs, dict):
        events_by_ap = dfs.get("events_by_ap", {})
        if events_by_ap:
            lines.append("DFS RADAR EVENTS:")
            for ap_name, events in events_by_ap.items():
                count = len(events) if isinstance(events, list) else events
                lines.append(f"  {ap_name}: {count} event(s)")
            lines.append("")

    # Band steering summary
    bs = analysis_data.get("band_steering_analysis", {})
    if isinstance(bs, dict) and bs.get("summary"):
        lines.append(f"BAND STEERING: {bs['summary']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are an expert network engineer analyzing a UniFi WiFi network. "
    "The user will provide their actual network analysis data as JSON. "
    "You MUST answer using ONLY that data — do not answer from general knowledge. "
    "Cite specific AP names, client names, channel numbers, RSSI values, and other "
    "metrics from the data. Be concise and actionable. "
    "If the data does not contain enough information to answer, say so briefly."
)

# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def _ask_ollama(question, context, model="llama3.2"):
    """Query a local Ollama instance using the chat endpoint. No API key required."""
    import requests

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        resp = requests.post(
            f"{host}/api/chat",
            json={
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Here is the network analysis data:\n{context}"
                            f"\n\nQuestion: {question}"
                        ),
                    },
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to Ollama at {host}.\n"
            "  • Install Ollama: https://ollama.com\n"
            f"  • Pull a model:   ollama pull {model}\n"
            "  • Start it:       ollama serve\n\n"
            "Or use a different backend:  --backend claude  |  --backend openai"
        )
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            raise RuntimeError(
                f"Model '{model}' not found in Ollama.\n" f"  Pull it first: ollama pull {model}"
            )
        raise RuntimeError(f"Ollama returned an error: {e}")
    return resp.json().get("message", {}).get("content", "").strip()


def _ask_claude(question, context, model="claude-opus-4-6"):
    """Query the Anthropic Claude API."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic package not installed. Run: pip install anthropic")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Get a key at https://console.anthropic.com"
        )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Network analysis data:\n{context}\n\nQuestion: {question}",
                }
            ],
        )
    except anthropic.AuthenticationError:
        raise RuntimeError(
            "Anthropic API key is invalid or expired. "
            "Check your key at https://console.anthropic.com"
        )
    except anthropic.PermissionDeniedError:
        raise RuntimeError(
            "Anthropic API access denied. Your credit balance may be too low. "
            "Add credits at https://console.anthropic.com → Plans & Billing"
        )
    except anthropic.BadRequestError as e:
        msg = str(e)
        if "credit balance" in msg.lower():
            raise RuntimeError(
                "Anthropic API credit balance is too low. "
                "Add credits at https://console.anthropic.com → Plans & Billing"
            )
        raise RuntimeError(f"Anthropic API error: {e}")
    except anthropic.APIError as e:
        raise RuntimeError(f"Anthropic API error: {e}")
    return response.content[0].text.strip()


def _ask_openai(question, context, model="gpt-4o"):
    """Query the OpenAI API."""
    try:
        import openai
    except ImportError:
        raise RuntimeError("openai package not installed. Run: pip install openai")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable not set.")

    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Network analysis data:\n{context}\n\nQuestion: {question}",
            },
        ],
    )
    return response.choices[0].message.content.strip()


_BACKENDS = {
    "ollama": _ask_ollama,
    "claude": _ask_claude,
    "openai": _ask_openai,
}

_MODEL_DEFAULTS = {
    "ollama": "llama3.2",
    "claude": "claude-opus-4-6",
    "openai": "gpt-4o",
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ask(question, analysis_data=None, recommendations=None, backend=None, model=None):
    """Ask a natural-language question about the network.

    If analysis_data is not provided, loads from the latest analysis cache
    in the working directory.

    Args:
        question: Plain-English question about the network.
        analysis_data: Optional dict from a prior analyze run. Loaded from
            cache if not supplied.
        recommendations: Optional list of recommendations. Loaded from cache
            if not supplied.
        backend: "ollama", "claude", or "openai". Defaults to config.yaml
            ai.backend (fallback: "ollama").
        model: Model name override. Defaults to config.yaml ai.model or the
            backend's built-in default.

    Returns:
        str: The AI's answer.

    Raises:
        RuntimeError: If no cache exists and analysis_data was not supplied,
            or if the backend is misconfigured.
        ValueError: If backend name is unknown.
    """
    from utils.config import get_config

    if analysis_data is None:
        analysis_data, recommendations = _load_latest_cache()
        if analysis_data is None:
            raise RuntimeError("No analysis cache found. Run 'python3 optimizer.py analyze' first.")

    if recommendations is None:
        recommendations = []

    ai_cfg = get_config().get("ai", {})
    backend = backend or ai_cfg.get("backend", "ollama")
    if backend not in _BACKENDS:
        raise ValueError(f"Unknown AI backend '{backend}'. Choose from: {', '.join(_BACKENDS)}")

    model = model or ai_cfg.get("model") or _MODEL_DEFAULTS.get(backend)
    context = _build_context(analysis_data, recommendations)
    return _BACKENDS[backend](question, context, model)


def generate_summary(analysis_data, recommendations, backend=None, model=None):
    """Generate a short plain-English executive summary for embedding in the report.

    Returns None when ai.summary_in_report is false (the default) or when the
    AI call fails for any reason. Callers must never depend on this returning a
    non-None value — it is always best-effort.
    """
    from utils.config import get_config

    ai_cfg = get_config().get("ai", {})
    if not ai_cfg.get("summary_in_report", False):
        return None

    prompt = (
        "Write a 3-4 sentence plain-English executive summary of this network's health. "
        "Mention the overall health score, the single most important problem, and one "
        "concrete action the owner should take. Write for a non-technical reader. "
        "No bullet points."
    )

    try:
        return ask(prompt, analysis_data, recommendations, backend, model)
    except Exception:
        return None  # Never block report generation
