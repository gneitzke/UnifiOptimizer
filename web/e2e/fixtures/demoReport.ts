/**
 * Demo report payload — PREVIEW / TEST FIXTURE ONLY, in the backend WIRE shape.
 *
 * This is exactly what `GET /api/report` returns (`netadmin/report/models.py`), so
 * routing it through the report page exercises the real path: the wire payload →
 * `fromWire` adapter → the section components. It is imported only by the report
 * e2e/validation harness, never by the shipped page, so the "no false data" gate
 * holds. The numbers are a plausible prosumer UniFi site chosen to exercise every
 * section, chart, severity level, and the honest data-gap.
 */

import type { WireReport } from '../../src/pages/report/wire';

const GEN = Math.floor(new Date('2026-07-21T09:15:00Z').getTime() / 1000);
const DAY = 86_400;
const START = GEN - 7 * DAY;

/** 7-day health trend; two middle buckets are omitted (an ingestion gap). */
function trend(): { ts: number; score: number }[] {
  const base = [94, 92, 95, 90, 88, 86, 83, 80, 78, 82, 85, 87, 84, 79, 76, 81, 84, 88, 90, 86, 83, 85, 88, 82, 79, 84, 87, 89];
  const step = (7 * DAY) / base.length;
  const out: { ts: number; score: number }[] = [];
  base.forEach((score, i) => {
    if (i === 13 || i === 14) return; // omitted buckets -> a real gap
    out.push({ ts: Math.round(START + i * step), score });
  });
  return out;
}

export const demoReport: WireReport = {
  cover: {
    site: 'Riverside Residence',
    generated_ts: GEN,
    tool: 'UnifiOptimizer',
    version: 'v0.2',
    window: { start_ts: START, end_ts: GEN, duration_s: 7 * DAY, label: '7 days' },
    counts: { aps: 3, switches: 1, gateways: 1, clients: 41 },
    confidentiality:
      'Confidential: network assessment for the site operator. Contains device and client identifiers.',
  },

  executive_summary: {
    verdict:
      'Attention advised: one high-severity finding is degrading the garden and garage, and 2.4 GHz congestion is dropping devices in the back bedroom during evening use.',
    scorecard: {
      health_score: 82,
      posture: 'attention advised',
      findings_by_severity: { critical: 0, high: 1, medium: 2, low: 1, info: 1 },
      total_findings: 5,
    },
    top_findings: [
      { id: 'WLAN-01', title: 'Garden AP mesh backhaul is weak and slow', severity: 'high', plain: '6 client(s) affected, about 6.7 client-hours degraded over the window.' },
      { id: 'WLAN-02', title: '2.4 GHz channel congestion on the Living Room AP', severity: 'medium', plain: '5 client(s) affected, about 2.1 client-hours degraded over the window.' },
      { id: 'INF-01', title: 'Receive errors on Core Switch port 7', severity: 'medium', plain: '2 client(s) affected, about 0.8 client-hours degraded over the window.' },
    ],
    recommendation_summary: '2 item(s) to address now, 1 soon, 1 strategic.',
  },

  scope: {
    data_sources: [
      'UniFi controller telemetry (stat/device, stat/sta, stat/health)',
      'Active local probes (DNS timing, gateway ICMP RTT)',
      'Neighbour scan (stat/rogueap)',
    ],
    sampling: [
      { source: 'stat/device (ports, radios, PoE, uplink)', cadence_s: 300, note: 'per-device poll' },
      { source: 'stat/sta (per-client RSSI, retries, roam)', cadence_s: 300, note: 'per-client poll' },
      { source: 'stat/health (WAN/www latency, drops)', cadence_s: 300, note: 'gateway poll' },
      { source: 'active probes (DNS, gateway RTT)', cadence_s: 60, note: 'local probe' },
      { source: 'stat/rogueap (neighbour BSS)', cadence_s: 86_400, note: 'daily scan' },
      { source: 'SLE minute accounting', cadence_s: 300, note: '5-minute buckets' },
    ],
    window: { start_ts: START, end_ts: GEN, duration_s: 7 * DAY, label: '7 days' },
    coverage: [
      { job: 'fast_device', interval_s: 300, fraction: 0.98, note: 'coverage adequate' },
      { job: 'fast_sta', interval_s: 300, fraction: 0.95, note: 'coverage adequate' },
      { job: 'fast_health', interval_s: 300, fraction: 0.91, note: 'coverage adequate' },
      { job: 'probe.gw_rtt', interval_s: 60, fraction: 0.44, note: 'under 50% live coverage; treat this window as partial' },
    ],
    limitations: [
      'No spectrum analyzer: non-Wi-Fi interference is inferred from airtime, not measured.',
      "No client-side RSSI: coverage is judged from the AP's view of the client.",
      'Neighbour APs are counted from a daily scan, so density is a sample, not continuous.',
      'WAN is measured by active probe and controller health, not a dedicated circuit monitor.',
      'A window under 50% poll coverage is reported as partial, never smoothed to look complete.',
    ],
  },

  inventory: {
    counts: { ap: 3, switch: 1, gateway: 1 },
    devices: [
      { entity_id: 1, name: 'UDM-SE', model: 'UDM-SE', role: 'gateway', uplink: null },
      { entity_id: 2, name: 'Core Switch', model: 'USW-Pro-24-PoE', role: 'switch', uplink: 'wire' },
      { entity_id: 3, name: 'Living Room AP', model: 'U6-Pro', role: 'ap', uplink: 'wire' },
      { entity_id: 4, name: 'Upstairs AP', model: 'U6-Lite', role: 'ap', uplink: 'wire' },
      { entity_id: 5, name: 'Garden AP', model: 'U6-Mesh', role: 'ap', uplink: 'wireless' },
    ],
  },

  topology: {
    gateway: { entity_id: 1, name: 'UDM-SE', model: 'UDM-SE', role: 'gateway', uplink: null, parent_id: null, mesh_uplink_rssi: null, backhaul_status: null, client_count: 0 },
    switches: [
      { entity_id: 2, name: 'Core Switch', model: 'USW-Pro-24-PoE', role: 'switch', uplink: 'wire', parent_id: 1, mesh_uplink_rssi: null, backhaul_status: null, client_count: 8 },
    ],
    aps: [
      { entity_id: 3, name: 'Living Room AP', model: 'U6-Pro', role: 'ap', uplink: 'wire', parent_id: 2, mesh_uplink_rssi: null, backhaul_status: null, client_count: 19 },
      { entity_id: 4, name: 'Upstairs AP', model: 'U6-Lite', role: 'ap', uplink: 'wire', parent_id: 2, mesh_uplink_rssi: null, backhaul_status: null, client_count: 14 },
      { entity_id: 5, name: 'Garden AP', model: 'U6-Mesh', role: 'ap', uplink: 'wireless', parent_id: 3, mesh_uplink_rssi: -68, backhaul_status: 'warn', client_count: 8 },
    ],
    backhaul_thresholds: { good_dbm: -65, warn_dbm: -70 },
  },

  health: {
    headline_score: 82,
    sles: [
      { sle: 'coverage', score: 88, total_minutes: 40320, fail_minutes: 277, top_offenders: [{ attributed_entity_id: 5, fail_minutes: 214, entity: { entity_id: 5, name: 'Garden AP', type: 'ap', native_id: 'ab:cd:ef:00:00:05', model: 'U6-Mesh' } }, { attributed_entity_id: 4, fail_minutes: 63, entity: { entity_id: 4, name: 'Upstairs AP', type: 'ap', native_id: 'ab:cd:ef:00:00:04', model: 'U6-Lite' } }] },
      { sle: 'capacity', score: 79, total_minutes: 40320, fail_minutes: 331, top_offenders: [{ attributed_entity_id: 3, fail_minutes: 331, entity: { entity_id: 3, name: 'Living Room AP', type: 'ap', native_id: 'ab:cd:ef:00:00:03', model: 'U6-Pro' } }] },
      { sle: 'connect', score: 91, total_minutes: 40320, fail_minutes: 88, top_offenders: [] },
      { sle: 'roaming', score: 74, total_minutes: 12040, fail_minutes: 402, top_offenders: [{ attributed_entity_id: 5, fail_minutes: 402, entity: { entity_id: 5, name: 'Garden AP', type: 'ap', native_id: 'ab:cd:ef:00:00:05', model: 'U6-Mesh' } }] },
      { sle: 'wan', score: 96, total_minutes: 10080, fail_minutes: 41, top_offenders: [] },
      { sle: 'infra', score: 90, total_minutes: 40320, fail_minutes: 47, top_offenders: [{ attributed_entity_id: 2, fail_minutes: 47, entity: { entity_id: 2, name: 'Core Switch', type: 'switch', native_id: 'ab:cd:ef:00:00:02', model: 'USW-Pro-24-PoE' } }] },
    ],
    trend: trend(),
  },

  rf: {
    utilization: [
      { entity_id: 31, ap_name: 'Living Room AP', band: '2.4', channel: 1, cu_total: 82, cu_self: 21, cu_non_self: 61 },
      { entity_id: 41, ap_name: 'Upstairs AP', band: '2.4', channel: 6, cu_total: 74, cu_self: 18, cu_non_self: 56 },
      { entity_id: 51, ap_name: 'Garden AP', band: '2.4', channel: 11, cu_total: 68, cu_self: 12, cu_non_self: 56 },
      { entity_id: 32, ap_name: 'Living Room AP', band: '5', channel: 44, cu_total: 44, cu_self: 22, cu_non_self: 22 },
      { entity_id: 42, ap_name: 'Upstairs AP', band: '5', channel: 36, cu_total: 31, cu_self: 15, cu_non_self: 16 },
      { entity_id: 52, ap_name: 'Garden AP', band: '5', channel: 149, cu_total: 22, cu_self: 10, cu_non_self: 12 },
    ],
    utilization_reference_pct: 70,
    neighbor_density: {
      total: 38,
      by_channel: [
        { band: '2.4', channel: 1, count: 14 },
        { band: '2.4', channel: 6, count: 11 },
        { band: '2.4', channel: 11, count: 9 },
        { band: '5', channel: 36, count: 4 },
        { band: '5', channel: 44, count: 3 },
        { band: '5', channel: 149, count: 2 },
        { band: '5', channel: 157, count: 5 },
      ],
      by_band: [
        { band: '2.4', count: 34 },
        { band: '5', count: 14 },
      ],
    },
    neighbor_summary:
      '38 neighbouring/rogue BSS(es) seen across 7 channel(s) in the scan window. A dense RF neighbourhood is environmental context, not a per-AP alarm.',
  },

  clients: {
    rssi_histogram: {
      bins: [
        { floor: null, ceil: -85, count: 2, weak: true },
        { floor: -85, ceil: -80, count: 4, weak: true },
        { floor: -80, ceil: -76, count: 5, weak: true },
        { floor: -76, ceil: -75, count: 3, weak: false },
        { floor: -75, ceil: -67, count: 11, weak: false },
        { floor: -67, ceil: -60, count: 8, weak: false },
        { floor: -60, ceil: -50, count: 3, weak: false },
        { floor: -50, ceil: null, count: 1, weak: false },
      ],
      total: 37,
      weak_count: 11,
      weak_threshold_dbm: -76,
      median_dbm: -70,
      min_dbm: -92,
      max_dbm: -48,
    },
    clients_per_ap: [
      { entity_id: 3, name: 'Living Room AP', client_count: 19 },
      { entity_id: 4, name: 'Upstairs AP', client_count: 14 },
      { entity_id: 5, name: 'Garden AP', client_count: 8 },
    ],
    worst_devices: [
      { entity: { entity_id: 101, name: 'Back-bedroom-iPad', type: 'client', native_id: '3c:22:fb:aa:bb:cc', model: null }, score: 8.4, fail_minutes: 214, issue_counts: { p2: 1, p3: 2, total: 3 }, event_count: 37 },
      { entity: { entity_id: 102, name: 'Garage-Camera', type: 'client', native_id: '9a:1f:22:00:44:55', model: null }, score: 6.9, fail_minutes: 158, issue_counts: { p3: 2, total: 2 }, event_count: 12 },
      { entity: { entity_id: 103, name: 'Office-Desktop', type: 'client', native_id: 'd8:cb:8a:77:88:99', model: null }, score: 5.1, fail_minutes: 92, issue_counts: { p2: 1, total: 1 }, event_count: 4 },
    ],
    clients_without_rssi: 4,
  },

  findings: [
    {
      id: 'WLAN-01',
      title: 'Garden AP mesh backhaul is weak and slow',
      severity: 'high',
      netadmin_severity: 'p1',
      detector_key: 'wifi.mesh_uplink',
      affected_assets: [
        { entity_id: 5, name: 'Garden AP', type: 'ap', role: 'ap' },
        { entity_id: 102, name: 'Garage-Camera', type: 'client', role: 'client' },
      ],
      observation:
        'The Garden AP reaches the network over a wireless mesh link that averaged −68 dBm and dipped into the warn band during evening use, below the level the backhaul needs to hold a fast rate.',
      evidence: { mesh_uplink_rssi_dbm: -68, backhaul_status: 'warn', fail_minutes: 402, confounders_checked: ['dfs_radar', 'ap_reboot'] },
      impact: { fail_minutes: 402, fail_client_hours: 6.7, affected_clients: 6, summary: '6 client(s) affected, 6.7 client-hours of failed SLE minutes over the window.' },
      root_cause:
        'The mesh backhaul shares the 5 GHz radio the Garden AP uses to serve clients, and the path crosses an exterior wall, so signal is marginal and contends with client traffic.',
      recommendation:
        'Run a wired uplink to the Garden AP if a cable path exists; if not, add a dedicated line-of-sight mesh point or pin the backhaul to the quieter channel 149 so it stops contending with client traffic.',
      confounders_checked: ['dfs_radar', 'ap_reboot'],
      signature: 'wifi.mesh_uplink:5',
      incident_id: 12,
      symptoms: [],
      source_issue_ids: [40, 41],
    },
    {
      id: 'WLAN-02',
      title: '2.4 GHz channel congestion on the Living Room AP',
      severity: 'medium',
      netadmin_severity: 'p2',
      detector_key: 'wifi.channel_plan',
      affected_assets: [{ entity_id: 3, name: 'Living Room AP', type: 'ap', role: 'ap' }],
      observation:
        'The Living Room AP 2.4 GHz radio ran at 82% airtime on channel 1, above the 70% point where latency climbs, with 34 neighbouring networks across the three usable channels.',
      evidence: { cu_total_pct: 82, channel: 1, neighbor_count_2g: 34 },
      impact: { fail_minutes: 128, fail_client_hours: 2.1, affected_clients: 5, summary: '5 client(s) affected, 2.1 client-hours of failed SLE minutes over the window.' },
      root_cause: 'A dense residential RF neighbourhood leaves little 2.4 GHz headroom, and the radio sits on channel 1, the busiest of the three.',
      recommendation:
        'Move the Living Room AP 2.4 GHz radio to channel 11 and set its width to 20 MHz; move any capable device onto 5 GHz, where airtime is under 45%.',
      confounders_checked: [],
      signature: 'wifi.channel_plan:3',
      incident_id: null,
      symptoms: [],
      source_issue_ids: [44],
    },
    {
      id: 'INF-01',
      title: 'Receive errors on Core Switch port 7',
      severity: 'medium',
      netadmin_severity: 'p2',
      detector_key: 'infra.port_errors',
      affected_assets: [{ entity_id: 2, name: 'Core Switch', type: 'switch', role: 'switch' }],
      observation: 'Core Switch port 7 logged 1,204 receive CRC errors over the window, climbing steadily. The port serves the Upstairs AP and the office desktop.',
      evidence: { rx_crc_errors: 1204, trend: 'rising', port: 7 },
      impact: { fail_minutes: 47, fail_client_hours: 0.8, affected_clients: 2, summary: '2 client(s) affected, 0.8 client-hours of failed SLE minutes over the window.' },
      root_cause: 'Rising CRC errors on a single port point to a marginal cable or connector, or a duplex mismatch, rather than a switch fault.',
      recommendation: 'Re-seat or replace the cable on Core Switch port 7, confirm auto-negotiation on both ends, and re-test; if errors persist on a known-good cable, move the uplink to a spare port and monitor.',
      confounders_checked: [],
      signature: 'infra.port_errors:2',
      incident_id: null,
      symptoms: [],
      source_issue_ids: [51],
    },
    {
      id: 'WLAN-03',
      title: 'Slow roaming between the Garden and Living Room APs',
      severity: 'low',
      netadmin_severity: 'p3',
      detector_key: 'wifi.roaming',
      affected_assets: [{ entity_id: 5, name: 'Garden AP', type: 'ap', role: 'ap' }],
      observation: 'Phones moving between the garden and living room took several seconds to roam, holding the weaker AP before switching. This correlates with WLAN-01.',
      evidence: { roaming_fail_minutes: 402, related_finding: 'WLAN-01' },
      impact: { fail_minutes: 0, fail_client_hours: 0, affected_clients: 0, summary: 'No failed SLE client-minutes are attributed to this finding over the window.' },
      root_cause: 'The weak mesh backhaul keeps the Garden AP’s effective signal low, so clients cling to the Living Room AP longer than they should.',
      recommendation: 'Resolving WLAN-01 removes most of this; once the backhaul is fixed, confirm 802.11r fast-roaming is enabled on the SSID.',
      confounders_checked: [],
      signature: 'wifi.roaming:5',
      incident_id: 12,
      symptoms: [],
      source_issue_ids: [46],
    },
    {
      id: 'ENV-01',
      title: 'RF neighbourhood and channel-plan contention',
      severity: 'info',
      netadmin_severity: 'p3',
      detector_key: 'wifi.rf_environment',
      affected_assets: [],
      observation: '2 strong neighbour AP(s) and 1 channel-plan contention point(s); 38 neighbour BSS(es) seen across 7 channel(s) over the window.',
      evidence: {
        neighbor_density_issue_count: 2,
        channel_plan_issue_count: 1,
        neighbor_bss_total: 38,
      },
      impact: { fail_minutes: 0, fail_client_hours: 0, affected_clients: 0, summary: 'No failed SLE client-minutes are attributed to this finding over the window.' },
      root_cause:
        'The site shares its RF neighbourhood with other networks and reuses channels across cells. Neighbour APs are counted from periodic scans, so this is environmental context, not a per-device fault.',
      recommendation: 'Keep slow and smart-home devices on the least-busy 2.4 GHz channel and lean on 5 GHz where the air is quiet. No per-neighbour action is warranted.',
      confounders_checked: [],
      signature: '',
      incident_id: null,
      symptoms: [],
      source_issue_ids: [60, 61, 62],
    },
  ],

  roadmap: {
    now: [
      { finding_id: 'WLAN-01', title: 'Garden AP mesh backhaul is weak and slow', severity: 'high', phase: 'now', text: 'Run a wired uplink to the Garden AP if a cable path exists; if not, add a dedicated line-of-sight mesh point or pin the backhaul to the quieter channel 149.' },
      { finding_id: 'INF-01', title: 'Receive errors on Core Switch port 7', severity: 'medium', phase: 'now', text: 'Re-seat or replace the cable on Core Switch port 7 and confirm auto-negotiation on both ends.' },
    ],
    soon: [
      { finding_id: 'WLAN-02', title: '2.4 GHz channel congestion on the Living Room AP', severity: 'medium', phase: 'soon', text: 'Move the Living Room AP 2.4 GHz radio to channel 11 and set its width to 20 MHz.' },
    ],
    strategic: [
      { finding_id: 'WLAN-03', title: 'Slow roaming between the Garden and Living Room APs', severity: 'low', phase: 'strategic', text: 'Once WLAN-01 is fixed, confirm 802.11r fast-roaming is enabled on the SSID.' },
    ],
  },

  appendix: {
    severity_rubric: [
      { level: 'critical', label: 'Critical', netadmin_source: 'P1 with measured SLE impact', meaning: 'Users are affected now; address before other work.', color_light: '#D70015', color_dark: '#FF6961' },
      { level: 'high', label: 'High', netadmin_source: 'P1 without measured impact, or P2 with impact', meaning: 'A real fault with user impact; schedule promptly.', color_light: '#C93400', color_dark: '#FFB340' },
      { level: 'medium', label: 'Medium', netadmin_source: 'P2 without measured impact', meaning: 'A confirmed problem without measured client impact yet.', color_light: '#B25000', color_dark: '#FFD426' },
      { level: 'low', label: 'Low', netadmin_source: 'P3', meaning: 'A minor or advisory item; fix when convenient.', color_light: '#1E7A34', color_dark: '#30DB5B' },
      { level: 'info', label: 'Info', netadmin_source: 'aggregated environmental context', meaning: 'Environmental context, not an action item on its own.', color_light: '#6E6E73', color_dark: '#8E8E93' },
    ],
    thresholds: {
      coverage_weak_dbm: -76,
      sticky_rssi_dbm: -67,
      capacity_degraded_pct: 80,
      wan_latency_abs_ms: 100,
      utilization_reference_pct: 70,
      backhaul_good_dbm: -65,
      backhaul_warn_dbm: -70,
      sle_weights: { coverage: 1.0, capacity: 1.0, connect: 1.5, roaming: 0.75, wan: 1.25, infra: 1.0 },
      health_trend_buckets: 96,
    },
    methodology: {
      scoring:
        'The health score and its breakdown are one GROUP BY over sle_minutes: a per-SLE score is ok-minutes over total-minutes, blended by weight.',
      attribution:
        'Failed client-minutes are pinned on an infrastructure entity only when the SLE engine attributed them by rule; unattributed minutes are excluded.',
      findings:
        'Correlated issues are grouped into one finding by the incident engine; neighbour and channel-plan noise is aggregated into one environmental finding.',
    },
    glossary: [
      { term: 'SLE', definition: 'Service-Level Expectation: a pass/fail judgement of a client-minute.' },
      { term: 'RSSI', definition: 'Received signal strength in dBm; closer to zero is stronger.' },
      { term: 'cu_total', definition: 'Channel airtime utilisation percentage on a radio.' },
      { term: 'Mesh backhaul', definition: 'The wireless uplink an AP uses instead of a wired drop.' },
      { term: 'Offender', definition: 'An entity ranked by the failed-minutes/issues/events it accounts for.' },
      { term: 'CVSS ladder', definition: 'Critical/High/Medium/Low/Info severity, mapped from P1/P2/P3.' },
    ],
  },

  generated_ts: GEN,
};
