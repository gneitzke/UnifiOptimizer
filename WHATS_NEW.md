# What's New in UnifiOptimizer

---

## v0.9.0 — 2026-02-20

### Versioning
- Added semantic versioning (`version.py` + `pyproject.toml`)
- Version shown in CLI (`--version` / `-V`) and in every HTML report header
- Starting at v0.9.0; will reach v1.0.0 once the feature set is considered stable

### Data Quality Banner & DFS Radar Exposure Card

### Data Quality Warning Banner
When the controller API returns errors during analysis (timeouts, permission failures, etc.), a banner now appears at the top of the report above the hero dashboard. Non-critical failures (timeouts) appear in amber; authentication/permission failures appear in red. The banner lists the affected endpoints and is suppressed entirely when the analysis completed cleanly.

### DFS Radar Exposure Card (RF Tab)
A new full-width card at the bottom of the RF & Airtime tab shows DFS radar events broken down by AP. Includes:
- Horizontal bar chart of events per AP, color-coded by severity (red > 5 events, amber > 2, green ≤ 2)
- Affected channel pills (52–144 flagged as DFS; 36–48 / 149–165 shown as safe)
- Explanatory note on what DFS means for clients (temporary drop to 2.4 GHz)
- Card is omitted entirely when no DFS events were detected during the lookback window

---

## v0.8.x — 2026-02-20 — Actionability & Client Insights

All changes are in `core/report_v2.py` (report generation only — no analysis logic changed).

### Quick Actions Bar
A compact pill-row now appears between the hero dashboard and the network topology, showing the top 3 recommended action groups at a glance — no scrolling required. Each pill is color-coded by priority (red = critical, yellow = important, blue = recommended) and links directly to the corresponding full action card below.

### Impact Counts on Action Cards
Every action card now shows how many clients or APs are affected ("Affects 6 clients", "3 APs"). Cards also carry anchor IDs (`#action-0`, `#action-1`, …) so the quick actions bar links work correctly.

### "Needs Attention" Client Spotlight
The Clients tab now opens with a compact "Needs Attention" subsection that surfaces the worst 5 wireless clients (health score < 70). Each row shows the client name, letter grade, numeric score, connected AP, and a color-coded issue badge that explains *why* the client is struggling:

| Badge | Condition |
|---|---|
| Weak signal | RSSI below −75 dBm |
| Frequent disconnects | > 5 disconnect events |
| On 2.4GHz | Channel ≤ 13 (band steering may help) |
| Excessive roaming | > 20 roam events |
| Low health score | Catch-all for other cases |

The section is omitted entirely when all clients score ≥ 70.

### Band Column in Client Table
The Problem Clients table now includes a Band column with color-coded pills:
- **2.4 GHz** — red when health score < 70 (likely a band steering candidate), grey otherwise
- **5 GHz** — green
- **6 GHz** — blue

---

## v0.7.x — Consolidation & Cleanup (PR #11)
- Merged duplicate analysis passes; unified recommendation pipeline
- Added `core/switch_analyzer.py` for switch port error/utilization analysis
- Fixed all Flake8 E226 and F401 warnings
- Updated `.gitignore` and `CLAUDE.md` for accuracy
