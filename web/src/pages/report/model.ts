/**
 * The report model contract (docs/ARCHITECTURE.md §19; docs/REPORT_SPEC.md).
 *
 * `GET /api/report` returns this whole shape, assembled by the backend from real
 * repository queries only (`netadmin/report/`). The report page renders it AS
 * GIVEN and computes nothing: every number, band, count and threshold here is a
 * backend field, so the two hard gates hold — no value is crunched in the UI, and
 * an absent field is shown as an honest "no data" state, never fabricated.
 *
 * Source of truth on drift is the backend assembler. Fields that a query may not
 * be able to fill are nullable / allowed-empty so a partial report still renders.
 */

/* ---- Severity (CVSS-aligned, five levels) ------------------------------- */

/**
 * The report's five-level CVSS severity. The backend maps netadmin's internal
 * P1/P2/P3 onto this per the SLE impact (P1→critical/high, P2→high/medium,
 * P3→low, advisories→info). Colours are resolved in `severity.ts`.
 */
export type ReportSeverity = 'critical' | 'high' | 'medium' | 'low' | 'info';

/** Score band, given by the backend — never derived in the UI from a raw score. */
export type Band = 'good' | 'fair' | 'poor' | 'none';

export interface SeverityCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
}

/* ---- 1. Cover / meta ---------------------------------------------------- */

export interface ReportWindow {
  start_ts: number;
  end_ts: number;
  /** Human window label, pre-formatted by the backend (e.g. "7 days"). */
  label: string;
}

export interface ReportMeta {
  /** Document title, e.g. "Network Assessment". */
  title: string;
  /** Site / network name being assessed. */
  site_name: string;
  /** When the report was generated (epoch seconds, UTC). */
  generated_ts: number;
  window: ReportWindow;
  /** Producing tool, "UnifiOptimizer". */
  tool: string;
  tool_version: string | null;
  /** Confidentiality line for the cover + running header. */
  confidentiality: string;
}

/* ---- 2. Executive summary ----------------------------------------------- */

export interface ExecFinding {
  id: string;
  title: string;
  /** Plain business-language impact (user experience, not dBm). */
  business_impact: string;
  severity: ReportSeverity;
}

export interface ExecutiveModel {
  /** One-sentence posture verdict. */
  verdict: string;
  overall_score: number | null;
  band: Band;
  severity_counts: SeverityCounts;
  top_findings: ExecFinding[];
  /** One-line prioritized recommendation summary. */
  recommendation_summary: string;
  /** Backend-computed window confidence — the caveat travels with the score. */
  coverage_pct: number | null;
  low_confidence: boolean;
  confidence_note: string | null;
}

/* ---- 3. Scope & methodology --------------------------------------------- */

export interface SamplingRow {
  source: string;
  cadence: string;
}

/** Measured poll coverage for one job over the window (honesty, not a guess). */
export interface CoverageRow {
  job: string;
  interval: string;
  /** 0..1 fraction of expected polls that actually landed. */
  fraction: number;
  note: string;
}

export interface ScopeModel {
  data_sources: string[];
  sampling: SamplingRow[];
  coverage: CoverageRow[];
  limitations: string[];
}

/* ---- 4. Inventory ------------------------------------------------------- */

export interface InventoryCounts {
  aps: number | null;
  switches: number | null;
  gateways: number | null;
  clients: number | null;
}

export interface DeviceRow {
  id: number | string;
  name: string;
  model: string | null;
  role: string;
  uplink: string | null;
}

export interface InventoryModel {
  counts: InventoryCounts;
  devices: DeviceRow[];
}

/* ---- 5. Topology -------------------------------------------------------- */

export type TopoLayerKind = 'internet' | 'gateway' | 'switch' | 'ap' | 'mesh' | 'client';

export interface TopoNode {
  id: string;
  label: string;
  sublabel: string | null;
  kind: TopoLayerKind;
  /** Optional short badge (e.g. a client count on an AP). */
  badge: string | null;
  /**
   * Wireless backhaul, present only for a mesh AP. The controller does not always
   * report the parent AP, so the uplink is shown ON the node (health-coloured
   * status + RSSI) rather than invented as an edge to a guessed parent.
   */
  mesh_uplink: { rssi: number | null; health: Band | null } | null;
}

export interface TopoLink {
  from: string;
  to: string;
  kind: 'wired' | 'mesh';
  /** Mesh backhaul RSSI (dBm); null for wired links. */
  rssi: number | null;
  /** Backhaul health, given by the backend — colours the mesh link. */
  health: Band | null;
  label: string | null;
}

export interface TopoLayer {
  kind: TopoLayerKind;
  label: string;
  nodes: TopoNode[];
}

export interface TopologyModel {
  layers: TopoLayer[];
  links: TopoLink[];
  note: string | null;
}

/* ---- 6. Health & performance -------------------------------------------- */

export interface TrendPoint {
  ts: number;
  /** null == a real data gap (rendered as a gap, never interpolated). */
  value: number | null;
}

export interface SleOffender {
  name: string;
  fail_minutes: number;
}

export interface SleScore {
  key: string;
  label: string;
  score: number | null;
  band: Band;
  top_offenders: SleOffender[];
  /** Backend flag: the window was under-observed, so a 100/100 here is unproven. */
  low_confidence: boolean;
}

export interface HealthModel {
  overall: { score: number | null; band: Band };
  trend: {
    points: TrendPoint[];
    summary_stat: string | null;
    as_of: number | null;
  };
  sles: SleScore[];
}

/* ---- 7. RF environment -------------------------------------------------- */

export interface RfChannelBar {
  band: string;
  channel: number | string;
  /** AP the radio belongs to — labels the bar so per-radio bars aren't ambiguous. */
  ap_name: string;
  utilization_pct: number | null;
}

export interface RfNeighborBar {
  band: string;
  channel: number | string;
  count: number | null;
}

export interface RfModel {
  utilization: RfChannelBar[];
  neighbors: RfNeighborBar[];
  /** Fixed reference threshold (e.g. 70%) — a backend constant, drawn as a line. */
  reference_pct: number | null;
  /** Aggregated, honestly-framed environmental context sentence. */
  summary: string | null;
  rogue_count: number | null;
}

/* ---- 8. Client analysis ------------------------------------------------- */

export interface RssiBin {
  label: string;
  min_dbm: number;
  max_dbm: number;
  count: number;
  /** The weak tail — coloured, given by the backend, not thresholded in the UI. */
  weak: boolean;
}

export interface ApLoad {
  ap_name: string;
  client_count: number;
}

export interface WorstDevice {
  name: string;
  metrics: EvidenceItem[];
}

export interface ClientsModel {
  rssi_histogram: RssiBin[];
  per_ap_load: ApLoad[];
  worst_devices: WorstDevice[];
  total_clients: number | null;
}

/* ---- 9. Findings (the fixed template) ----------------------------------- */

export interface EvidenceItem {
  label: string;
  value: string;
}

export interface EvidenceBar {
  label: string;
  value: number | null;
  /** Optional per-bar status for the accent/status ramp. */
  status: ReportSeverity | 'good' | null;
}

/**
 * An optional chart carried by a finding as its measured evidence (e.g. the
 * mesh-uplink-RSSI trend). Series are pre-computed; the UI only draws them.
 */
export interface EvidenceChart {
  kind: 'timeseries' | 'bars';
  context_label: string;
  summary_stat: string | null;
  unit: string | null;
  percentage: boolean;
  reference: { value: number; label: string | null } | null;
  points: TrendPoint[] | null;
  bars: EvidenceBar[] | null;
  takeaway: string | null;
}

export interface Finding {
  /** Stable id, e.g. "WLAN-03". */
  id: string;
  title: string;
  severity: ReportSeverity;
  affected: string[];
  /** The measured fact + its evidence. */
  observation: string;
  evidence: EvidenceItem[];
  evidence_chart: EvidenceChart | null;
  /** Impact in user-experience terms (fail-minutes, affected clients). */
  impact: string;
  root_cause: string;
  /** Specific, ordered recommendation steps. */
  recommendation: string[];
  /** Correlated-incident id when the finding groups symptoms under a root. */
  incident_id: number | null;
}

/* ---- 10. Recommendations / roadmap -------------------------------------- */

export type Phase = 'now' | 'soon' | 'strategic';

export interface Recommendation {
  finding_id: string;
  severity: ReportSeverity;
  phase: Phase;
  text: string;
}

/* ---- 11. Appendix ------------------------------------------------------- */

export interface RubricRow {
  severity: ReportSeverity;
  definition: string;
}

export interface ThresholdRow {
  name: string;
  value: string;
  source: string | null;
}

export interface GlossaryRow {
  term: string;
  definition: string;
}

export interface AppendixModel {
  rubric: RubricRow[];
  thresholds: ThresholdRow[];
  glossary: GlossaryRow[];
  methodology_detail: string[];
}

/* ---- The whole report --------------------------------------------------- */

export interface ReportModel {
  meta: ReportMeta;
  executive: ExecutiveModel;
  scope: ScopeModel;
  inventory: InventoryModel;
  topology: TopologyModel | null;
  health: HealthModel;
  rf: RfModel;
  clients: ClientsModel;
  findings: Finding[];
  recommendations: Recommendation[];
  appendix: AppendixModel;
}
