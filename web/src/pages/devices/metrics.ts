/**
 * Metric presentation catalog for the inventory drill-downs.
 *
 * The backend ships raw series keys (`cu_total`, `uplink_rssi`, ...); this maps
 * each to a human label, a display unit, precision, and chart hints so a device
 * or client page never shows a bare, unlabelled number (never-do rule 5). Units
 * live in the header, values are tabular (docs §Typography). Unknown keys fall
 * back to a title-cased label rather than being hidden — honest over lossy.
 */

import type { Sample } from './api';

export interface MetricMeta {
  label: string;
  /** Display unit shown once, in a header/axis — not repeated per cell. */
  unit?: string;
  digits: number;
  /** Fixed 0-100 axis (Apple battery pattern) for percentage series. */
  percentage?: boolean;
  /** Which direction is healthier; enables the MetricTile delta tone. */
  good?: 'up' | 'down';
  /** A fixed, meaning-anchored y-domain (dBm signal levels). Pins the axis so the
   *  same reading looks the same on a 24h and a 7d chart and never floats to hug
   *  the data max (which makes a genuinely-poor level read as unremarkable). */
  domain?: { min: number; max: number };
  /** A threshold reference line (e.g. the "poor" RSSI level). */
  reference?: { value: number; label: string };
}

export const METRIC_META: Record<string, MetricMeta> = {
  // AP / device level
  cpu: { label: 'CPU', unit: '%', digits: 0, percentage: true, good: 'down' },
  mem: { label: 'Memory', unit: '%', digits: 0, percentage: true, good: 'down' },
  num_sta: { label: 'Clients', unit: '', digits: 0 },
  satisfaction: { label: 'Satisfaction', unit: '%', digits: 0, percentage: true, good: 'up' },
  uplink_rssi: {
    label: 'Uplink RSSI',
    unit: 'dBm',
    digits: 0,
    good: 'up',
    domain: { min: -90, max: -30 },
    reference: { value: -70, label: '−70 poor' },
  },
  uplink_tx_rate: { label: 'Uplink TX', unit: 'kbps', digits: 0, good: 'up' },
  rx_bytes: { label: 'RX', unit: 'B/s', digits: 0 },
  tx_bytes: { label: 'TX', unit: 'B/s', digits: 0 },

  // Radio
  cu_total: { label: 'Channel utilization', unit: '%', digits: 0, percentage: true, good: 'down' },
  cu_self_rx: { label: 'Self RX airtime', unit: '%', digits: 0, percentage: true, good: 'down' },
  cu_self_tx: { label: 'Self TX airtime', unit: '%', digits: 0, percentage: true, good: 'down' },
  tx_retries: { label: 'TX retries', unit: 'pkts', digits: 0, good: 'down' },

  // Port
  poe_power: { label: 'PoE draw', unit: 'W', digits: 1 },
  rx_errors: { label: 'RX errors', unit: 'pkts', digits: 0, good: 'down' },
  tx_errors: { label: 'TX errors', unit: 'pkts', digits: 0, good: 'down' },
  rx_dropped: { label: 'RX dropped', unit: 'pkts', digits: 0, good: 'down' },
  tx_dropped: { label: 'TX dropped', unit: 'pkts', digits: 0, good: 'down' },
  rx_broadcast: { label: 'RX broadcast', unit: 'pkts', digits: 0 },
  tx_broadcast: { label: 'TX broadcast', unit: 'pkts', digits: 0 },
  rx_multicast: { label: 'RX multicast', unit: 'pkts', digits: 0 },
  tx_multicast: { label: 'TX multicast', unit: 'pkts', digits: 0 },

  // Client
  rssi: {
    label: 'Signal (RSSI)',
    unit: 'dBm',
    digits: 0,
    good: 'up',
    // Wi-Fi RSSI meaningful range: −30 (excellent, adjacent) to −90 (unusable).
    // −70 dBm is the widely-used "poor / below reliable" threshold.
    domain: { min: -90, max: -30 },
    reference: { value: -70, label: '−70 poor' },
  },
  noise: { label: 'Noise floor', unit: 'dBm', digits: 0, good: 'down' },
  rx_rate: { label: 'RX rate', unit: 'kbps', digits: 0, good: 'up' },
  tx_rate: { label: 'TX rate', unit: 'kbps', digits: 0, good: 'up' },
  roam_count: { label: 'Roams', unit: '', digits: 0 },
  wifi_tx_attempts: { label: 'TX attempts', unit: 'pkts', digits: 0 },
};

export function metricMeta(key: string): MetricMeta {
  return METRIC_META[key] ?? { label: titleCase(key), digits: 0 };
}

function titleCase(key: string): string {
  return key
    .split('_')
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}

/** Index an entity's sample list by metric key for O(1) lookups. */
export function sampleMap(samples: Sample[]): Map<string, Sample> {
  const m = new Map<string, Sample>();
  for (const s of samples) m.set(s.metric, s);
  return m;
}

/** The metrics a device rollup surfaces as compact tiles, per device type. */
export const AP_HEADLINE = ['satisfaction', 'num_sta', 'cpu', 'mem'];
export const SWITCH_HEADLINE = ['cpu', 'mem'];
export const GATEWAY_HEADLINE = ['cpu', 'mem'];

/** The metrics charted (with a 24h/7d toggle) on an AP detail. */
export const AP_CHART_METRICS = ['satisfaction', 'num_sta', 'cpu', 'mem'];
/** Charted per-radio. */
export const RADIO_CHART_METRIC = 'cu_total';
/** Charted on a client detail. */
export const CLIENT_CHART_METRICS = ['rssi', 'satisfaction'];
