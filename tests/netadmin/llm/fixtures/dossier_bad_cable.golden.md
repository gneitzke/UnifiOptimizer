# Investigation dossier — issue #1

**rx_errors climbing on Port 5**

| Field | Value |
| --- | --- |
| Detector | `wired.bad_cable` |
| Severity | P2 |
| State | active |
| Entity | Port 5 (port, aa:bb:cc:00:00:02:5) |
| First seen | <TS> |
| Last seen | <TS> |
| Lifetime | ongoing 10m |
| Occurrences | 3 |
| Fix state | — |
| Fingerprint | `fp-bad-cable` |

## Lifecycle trail

| When | Event | Detail |
| --- | --- | --- |
| <TS> | detected | severity=p2 |
| <TS> | escalated | m=3 |

## Evidence

| Metric | Value | Unit |
| --- | --- | --- |
| negotiated_mbps | 100 | Mbps |
| rx_errors_per_min | 42 | /min |

## Confounders ruled out

The detector tested and rejected these false-positive traps:

- Known 100mbps device
- Counter age
- Unmanaged switch hop

**Traps this class of problem is known for:** Known 100 Mbps device classes; counter age (a stale cumulative counter); an unmanaged-switch hop hiding the real port.

## Related issues

### On Port 5 (this entity)

| Issue | Detector | Sev | State | First seen | Title |
| --- | --- | --- | --- | --- | --- |
| #2 | `wired.port_flapping` | P2 | active | <TS> | Port 5 flapping |

### On parent sw-core

| Issue | Detector | Sev | State | First seen | Title |
| --- | --- | --- | --- | --- | --- |
| #3 | `wired.poe_budget` | P2 | active | <TS> | PoE budget pressure on sw-core |

## Metric windows around first seen

Bucketed hourly stats, ±3 h around <TS> (windows, not raw samples).

### rx_dropped_pct (%) — tier: raw

| Hour (UTC) | n | min | mean | max |
| --- | --- | --- | --- | --- |
| <TS> | 10 | 1 | 3 | 5 |
| <TS> | 12 | 1 | 2.75 | 5 |
| <TS> | 2 | 3 | 3.5 | 4 |

## Site context

Inventory: 1 ap, 1 port, 1 switch.

| Device | Type | Model | Children |
| --- | --- | --- | --- |
| sw-core | switch | — | 1 |
| ap-office | ap | U6-Pro | 0 |

> **Gateway-less site:** no gateway in inventory. WAN latency/loss/DNS detectors run against controller health and local probes only — treat WAN attribution as best-effort.

## Detector playbook — `wired.bad_cable`

- **Signature:** rx_errors delta rate > 10/min sustained or > 0.001% of packets; OR a gigabit-capable peer negotiated at 10/100 (broken-pair downshift).
- **Confounders to rule out:** Known 100 Mbps device classes; counter age (a stale cumulative counter); an unmanaged-switch hop hiding the real port.
- **Fix guidance:** Reseat then replace the patch cable; re-test the run. On an uplink port this is P1 — the whole segment rides it.

## STRUCTURED QUESTIONS

Answer as a network admin who remembers this issue's history. Respond in
**Markdown**, beginning with a `## Answers` heading, and use these `### `
subheadings verbatim so the response can be parsed loosely:

### Root cause
The single most likely root cause, given the evidence above and the confounders
already ruled out. State what the dossier does *not* yet prove.

### Evidence to collect next
The one or two additional signals that would confirm or refute that root cause.

### Recommended fix and risk
The one change you would make and its risk. This tool never applies changes
automatically — recommend, do not act.

### Confidence
`low` / `medium` / `high`, with one sentence of justification.
