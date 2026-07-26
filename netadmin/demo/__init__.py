"""Demo-dataset generation for netadmin.

A pure, deterministic generator that writes a fully-populated demo SQLite
database representing a *fictional* small home/prosumer network -- so the public
repository can show realistic screenshots and a live demo with **none** of the
owner's real network data (no real MACs, hostnames, IPs, or room names).

Everything here is data generation only: it opens a fresh database through
:class:`netadmin.store.repository.Repository`, writes inventory / series /
samples+rollups / events / poll_runs / issues+issue_events / changes / baselines /
sle_minutes / investigations, and never touches a controller, the network, or
MQTT. The generator is seeded from a fixed constant and anchors all timestamps to
a fixed baseline ``now`` (overridable) so regenerating the demo is byte-stable in
its counts and never churns on the wall clock.
"""

from __future__ import annotations

from netadmin.demo.seed import DEFAULT_HISTORY_DAYS, DEFAULT_NOW, DEMO_SEED, seed_demo

__all__ = [
    "seed_demo",
    "DEFAULT_NOW",
    "DEMO_SEED",
    "DEFAULT_HISTORY_DAYS",
]
