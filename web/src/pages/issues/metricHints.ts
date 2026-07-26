/**
 * Maps an issue's detector + evidence to the metric series worth charting on its
 * detail page ("related metric chart ... using evidence's series hints").
 *
 * Series names below are the real ones the ingest layer writes
 * (`netadmin/ingest/mapping.py`), charted on the issue's own entity. An explicit
 * hint in evidence always wins; when neither a hint nor a mapping applies (or the
 * series simply was never recorded → the metrics endpoint 404s) the detail page
 * shows an honest "no linked series" note rather than inventing a chart.
 */

import type { IssueRow } from '../shared/api';

export interface MetricHint {
  entityId: number;
  metric: string;
  label: string;
  unit?: string;
  /** Lock the y-axis to 0-100 (utilization / satisfaction). */
  percentage?: boolean;
}

/** detector_key -> the metric(s) to chart on the issue's entity. */
const DETECTOR_METRICS: Record<
  string,
  Array<{ metric: string; label: string; unit?: string; percentage?: boolean }>
> = {
  'wan.isp_degraded': [{ metric: 'wan_latency', label: 'WAN latency', unit: 'ms' }],
  'wan.dns_slow': [{ metric: 'dns_latency_ms', label: 'DNS resolution time', unit: 'ms' }],
  'wan.bufferbloat': [{ metric: 'gw_rtt_ms', label: 'Gateway RTT', unit: 'ms' }],
  'wan.flapping': [{ metric: 'wan_drops', label: 'WAN drops', unit: '/min' }],
  'wired.bad_cable': [
    { metric: 'rx_errors', label: 'RX errors', unit: '/min' },
    { metric: 'tx_errors', label: 'TX errors', unit: '/min' },
  ],
  'wired.duplex_mismatch': [{ metric: 'rx_errors', label: 'RX errors', unit: '/min' }],
  'wired.uplink_saturation': [{ metric: 'tx_bytes', label: 'TX throughput', unit: 'B/s' }],
  'wired.poe_budget': [{ metric: 'total_used_power', label: 'PoE used power', unit: 'W' }],
  'wired.sfp_degraded': [
    { metric: 'sfp_rxpower', label: 'SFP RX power', unit: 'dBm' },
    { metric: 'sfp_txpower', label: 'SFP TX power', unit: 'dBm' },
    { metric: 'sfp_temperature', label: 'SFP module temperature', unit: '°C' },
    { metric: 'sfp_current', label: 'SFP bias current', unit: 'mA' },
  ],
  'infra.device_overheating': [
    { metric: 'temp', label: 'Chassis temperature', unit: '°C' },
    { metric: 'fan_level', label: 'Fan level' },
  ],
  'wifi.airtime_saturation': [
    { metric: 'cu_total', label: 'Channel utilization', unit: '%', percentage: true },
  ],
  'wifi.sticky_client': [{ metric: 'rssi', label: 'Client RSSI', unit: 'dBm' }],
  'client.flaky': [{ metric: 'rssi', label: 'Client RSSI', unit: 'dBm' }],
};

function fromEvidenceHints(evidence: Record<string, unknown>, fallbackEntity: number | null): MetricHint[] {
  const raw = evidence['series_hints'];
  if (!Array.isArray(raw)) return [];
  const out: MetricHint[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const h = item as Record<string, unknown>;
    const metric = typeof h.metric === 'string' ? h.metric : undefined;
    const entityId =
      typeof h.entity_id === 'number' ? h.entity_id : fallbackEntity ?? undefined;
    if (!metric || entityId == null) continue;
    out.push({
      entityId,
      metric,
      label: typeof h.label === 'string' ? h.label : metric,
      unit: typeof h.unit === 'string' ? h.unit : undefined,
      percentage: h.percentage === true,
    });
  }
  return out;
}

export function metricHintsForIssue(issue: IssueRow): MetricHint[] {
  const evidence = issue.evidence ?? {};
  const entityId = issue.entity_id;

  // 1. Explicit structured hints in evidence win.
  const explicit = fromEvidenceHints(evidence, entityId);
  if (explicit.length) return explicit.slice(0, 2);

  // 2. A single `metric` string in evidence, charted on the owning entity.
  if (typeof evidence['metric'] === 'string' && entityId != null) {
    return [{ entityId, metric: evidence['metric'] as string, label: evidence['metric'] as string }];
  }

  // 3. Curated per-detector default, on the owning entity.
  if (entityId != null) {
    const mapped = DETECTOR_METRICS[issue.detector_key];
    if (mapped) return mapped.map((m) => ({ entityId, ...m }));
  }
  return [];
}
