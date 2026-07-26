# How UnifiOptimizer compares

An honest map of the UniFi tooling landscape and where this project sits in it.
Figures were checked on 2026-07-25; star counts move, so treat them as a snapshot
rather than a scoreboard. If something here is wrong or out of date, open an issue
and it gets fixed.

The short version: several of these tools are better than UnifiOptimizer at the
thing they were built for, and this page says which. Running more than one is a
perfectly reasonable answer.

## The four shapes

Almost every UniFi tool falls into one of four shapes.

**Metrics pipelines** collect everything and interpret nothing. You get raw series
and build the meaning yourself in a dashboard. [unpoller](https://github.com/unpoller/unpoller)
is the mature example: a Go collector feeding InfluxDB or Prometheus with twelve
Grafana dashboards. It remembers without understanding.

**Live-query tools** interpret the present well but inherit the controller's
memory, which is roughly a day of fine-grained stats. The controller's own views,
UniFi's WiFi Agent, WiFiman, and the UniFi MCP servers all sit here. They
understand without remembering, so "when did this start" is not a question they
can answer.

**Snapshot reporters** run once and print a picture of right now.
[Unifi_Network_Health_Report](https://github.com/FryguyPA/Unifi_Network_Health_Report)
does this well, and it is what UnifiOptimizer itself used to be before the rebuild.

**Suites** do many things across security, RF, WAN and monitoring.
[NetworkOptimizer](https://github.com/Ozark-Connect/NetworkOptimizer) is the
strongest one, and it is genuinely broader than this project.

UnifiOptimizer is a fifth shape, and a narrow one: it keeps history **and** attaches
semantics to it. Findings become tracked issues with a lifecycle, correlated into
incidents with a root cause, and scored by how many client-minutes they actually
cost. That is the whole product. Everything else here is a consequence of it.

## At a glance

| | UnifiOptimizer | [unpoller](https://github.com/unpoller/unpoller) | [NetworkOptimizer](https://github.com/Ozark-Connect/NetworkOptimizer) | [unifi-network-mcp](https://github.com/sirkirby/unifi-network-mcp) | [UI Toolkit](https://github.com/Crosstalk-Solutions/unifi-toolkit) | Native controller |
|---|---|---|---|---|---|---|
| Stars (2026-07-25) | new | 2.7k | 910 | 571 | 496 | n/a |
| License | MIT | MIT | BSL-1.1, free ≤3 sites | MIT | MIT | proprietary |
| Stack | Python + React | Go | .NET 10 / Blazor | Python / TS | FastAPI | n/a |
| Keeps history | 30d raw, 18mo hourly, daily forever | yes, via InfluxDB/Prometheus | yes, SQLite + InfluxDB | no, live query | yes, SQLite | about 1 day fine-grained |
| Detection | 33 detectors with recorded confounder checks | none | 83 security checks, RF, ISP | health checks, firewall audit | Wi-Fi tracking, IDS view | built-in alerts |
| Issue lifecycle | pending → active → resolving → resolved, dedup, reopen, inhibition | no | no | no | no | no |
| Root-cause correlation | yes, incidents group cause with symptoms | no | no | event correlation | no | no |
| User-impact scoring | Mist-style user-minutes, exclusive attribution | no | scores, not user-minutes | no | no | no |
| Applies fixes | approval-gated, snapshotted, revertible | no | yes | yes, preview then confirm | block/unblock | yes |
| AI | optional, any model, plus a read-only MCP server | no | no | yes, this is the point of it | no | some |
| Extra infra | none, one SQLite file | InfluxDB or Prometheus, plus Grafana | SQLite, optional InfluxDB | none | none | n/a |

## Where the others are better

**unpoller** is the better metrics pipeline, and it is not close. If what you want
is every counter the controller exposes, landing in Prometheus or InfluxDB with
mature Grafana dashboards on top, use unpoller. It has years of hardening and a far
larger install base. UnifiOptimizer deliberately does not export metrics or ship
Grafana dashboards.

**NetworkOptimizer** is broader on nearly every axis: security auditing with a
scored report, RF heatmaps and signal mapping, SNMP polling, 2D and 3D topology,
multi-WAN speed testing, Starlink dish telemetry, WAN steering, adaptive SQM,
IPS and threat analysis with CrowdSec, multi-site support, and installers for
Docker, NAS, Proxmox and Home Assistant. If you want one tool that covers the most
ground, that is the one. Two caveats worth knowing rather than arguing: it is
BSL-1.1, so free use stops at three sites and commercial use needs a license, and
it has no AI or LLM component.

**unifi-network-mcp** is the better way to give an assistant hands on your
controller. It exposes 186 Network tools plus Protect and Access, with a
preview-then-confirm write flow, and it installs from the Claude Code plugin
marketplace. UnifiOptimizer's MCP server does not compete with it and deliberately
exposes no live-controller tools at all. Theirs is Claude's hands; ours is Claude's
memory, and running both together is the intended setup.

**UI Toolkit** has per-device Wi-Fi tracking (follow one MAC across APs with roam
detection) and a clean IDS/IPS threat view, neither of which this project has.

**The native controller** is better at configuration, provisioning, topology and
anything requiring vendor internals. UnifiOptimizer reads it and never replaces it.

## Where UnifiOptimizer is different

Four things, and no other tool in the table has any of them.

**Issues have a lifecycle.** A finding does not become a new alert every poll. It
gets a fingerprint, one open issue exists per fingerprint, it moves through states,
it reopens the same row if the fault refires within a day, and a bigger fault
suppresses the smaller ones it explains. A downed switch mutes its own ports.

**Confounder checks are recorded, not just evaluated.** The bad-cable detector
fires on a gigabit port stuck at 100 Mbps only after confirming the port is truly
gigabit-capable and the attached device is not a known 100 Mbps class, and it stores
those checks with the finding. You can audit why it decided.

**Correlation produces incidents.** A failing mesh uplink reads as one incident with
a root cause and its symptoms, rather than six unrelated complaints.

**Health decomposes.** Scores are failed client-minutes attributed to exactly one
cause on one device, so a number always has a receipt. An idle client with bad
signal contributes zero failed minutes, because nobody was inconvenienced.

## Choosing

- You want dashboards and raw metrics: **unpoller**.
- You want the widest feature coverage in one tool: **NetworkOptimizer**.
- You want an assistant that can change your network: **unifi-network-mcp**.
- You want to follow one device's Wi-Fi behaviour, or watch IDS events: **UI Toolkit**.
- You want a one-off report with no daemon: **Unifi_Network_Health_Report**.
- You want problems tracked over time, explained, and scored by user impact, with
  fixes you approve and can revert: **UnifiOptimizer**.

## What UnifiOptimizer will not do

Stated so the scope is predictable: no security-posture auditing, no RF heatmaps,
no SNMP, no multi-site, no metrics export, no interactive topology, and no AI with
write access to your network. Those are covered well by the projects above.
