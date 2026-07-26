/**
 * Wire types — the exact JSON shape `GET /api/report` returns.
 *
 * These mirror the backend dataclasses in `netadmin/report/models.py` field for
 * field (the source of truth). The report page renders the VIEW model in
 * `model.ts`; `fromWire.ts` maps this wire shape to it. Keeping the two apart is
 * the codebase's established reconciliation pattern (see `pages/shared/api.ts`):
 * the wire type tracks the backend, the view type tracks what the sections draw,
 * and one small adapter bridges them without any component knowing about drift.
 *
 * The adapter only renames and restructures — it never derives a metric — so every
 * number the UI shows still traces to a backend field (the "no false data" gate).
 */

export interface WireWindow {
  start_ts: number;
  end_ts: number;
  duration_s: number;
  label: string;
}

export interface WireCover {
  site: string;
  generated_ts: number;
  tool: string;
  version: string;
  window: WireWindow;
  counts: Record<string, number>; // { aps, switches, gateways, clients }
  confidentiality: string;
}

export interface WireScorecard {
  health_score: number | null;
  posture: string;
  findings_by_severity: Record<string, number>; // { critical, high, medium, low, info }
  total_findings: number;
  coverage_pct: number | null;
  low_confidence: boolean;
  confidence_note: string | null;
}

export interface WireTopFinding {
  id: string;
  title: string;
  severity: string;
  plain: string;
}

export interface WireExecutive {
  verdict: string;
  scorecard: WireScorecard;
  top_findings: WireTopFinding[];
  recommendation_summary: string;
}

export interface WireSampling {
  source: string;
  cadence_s: number | null;
  note: string;
}

export interface WireCoverage {
  job: string;
  interval_s: number;
  fraction: number;
  note: string;
}

export interface WireScope {
  data_sources: string[];
  sampling: WireSampling[];
  window: WireWindow;
  coverage: WireCoverage[];
  limitations: string[];
}

export interface WireInventoryDevice {
  entity_id: number;
  name: string;
  model: string | null;
  role: string;
  uplink: string | null;
}

export interface WireInventory {
  counts: Record<string, number>; // { ap, switch, gateway }
  devices: WireInventoryDevice[];
}

export interface WireTopoNode {
  entity_id: number;
  name: string;
  model: string | null;
  role: string;
  uplink: string | null; // "wire" | "wireless" | null
  parent_id: number | null;
  mesh_uplink_rssi: number | null;
  backhaul_status: string | null; // "good" | "warn" | "bad" | "unknown" | null
  client_count: number;
}

export interface WireTopology {
  gateway: WireTopoNode | null;
  switches: WireTopoNode[];
  aps: WireTopoNode[];
  backhaul_thresholds: Record<string, number>;
}

export interface WireEntityRef {
  entity_id: number;
  name: string | null;
  type: string | null;
  native_id: string | null;
  model: string | null;
}

export interface WireSleOffender {
  attributed_entity_id: number | null;
  fail_minutes: number;
  entity: WireEntityRef | null;
}

export interface WireSle {
  sle: string;
  score: number | null; // 0..100
  total_minutes: number;
  fail_minutes: number;
  top_offenders: WireSleOffender[];
  low_confidence: boolean;
}

export interface WireTrendPoint {
  ts: number;
  score: number; // 0..100
}

export interface WireHealth {
  headline_score: number | null;
  sles: WireSle[];
  trend: WireTrendPoint[];
}

export interface WireRadioUtil {
  entity_id: number;
  ap_name: string;
  band: string | null;
  channel: string | number | null;
  cu_total: number | null; // percentage 0..100
  cu_self: number | null;
  cu_non_self: number | null;
}

export interface WireNeighborChannel {
  band: string | null;
  channel: string | number | null;
  count: number;
}

export interface WireNeighborDensity {
  total: number;
  by_channel: WireNeighborChannel[];
  by_band: { band: string | null; count: number }[];
}

export interface WireRf {
  utilization: WireRadioUtil[];
  utilization_reference_pct: number;
  neighbor_density: WireNeighborDensity;
  neighbor_summary: string;
}

export interface WireHistogramBin {
  floor: number | null;
  ceil: number | null;
  count: number;
  weak: boolean;
}

export interface WireHistogram {
  bins: WireHistogramBin[];
  total: number;
  weak_count: number;
  weak_threshold_dbm: number;
  median_dbm: number | null;
  min_dbm: number | null;
  max_dbm: number | null;
}

export interface WireApLoad {
  entity_id: number;
  name: string;
  client_count: number;
}

export interface WireWorstDevice {
  entity: WireEntityRef | null;
  score: number;
  fail_minutes: number;
  issue_counts: Record<string, number>;
  event_count: number;
}

export interface WireClients {
  rssi_histogram: WireHistogram;
  clients_per_ap: WireApLoad[];
  worst_devices: WireWorstDevice[];
  clients_without_rssi: number;
}

export interface WireAffectedAsset {
  entity_id: number;
  name: string;
  type: string;
  role: string;
}

export interface WireFinding {
  id: string;
  title: string;
  severity: string;
  netadmin_severity: string | null;
  detector_key: string;
  affected_assets: WireAffectedAsset[];
  observation: string;
  evidence: Record<string, unknown>;
  impact: {
    fail_minutes: number;
    fail_client_hours: number;
    affected_clients: number;
    summary: string;
  };
  root_cause: string;
  recommendation: string;
  confounders_checked: string[];
  signature: string;
  incident_id: number | null;
  symptoms: unknown[];
  source_issue_ids: number[];
}

export interface WireRecommendation {
  finding_id: string;
  title: string;
  severity: string;
  phase: string;
  text: string;
}

export interface WireRoadmap {
  now: WireRecommendation[];
  soon: WireRecommendation[];
  strategic: WireRecommendation[];
}

export interface WireRubricRow {
  level: string;
  label: string;
  netadmin_source: string;
  meaning: string;
  color_light: string;
  color_dark: string;
}

export interface WireAppendix {
  severity_rubric: WireRubricRow[];
  thresholds: Record<string, unknown>;
  methodology: Record<string, string>;
  glossary: { term: string; definition: string }[];
}

export interface WireReport {
  cover: WireCover;
  executive_summary: WireExecutive;
  scope: WireScope;
  inventory: WireInventory;
  topology: WireTopology;
  health: WireHealth;
  rf: WireRf;
  clients: WireClients;
  findings: WireFinding[];
  roadmap: WireRoadmap;
  appendix: WireAppendix;
  generated_ts: number;
}
