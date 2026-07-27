/**
 * Wire → view adapter: map the backend's `GET /api/report` payload
 * (`wire.ts` / `netadmin/report/models.py`) to the view model the sections render
 * (`model.ts`). This is a pure rename/restructure — it never derives a metric, so
 * every number the report shows still traces to a backend field. The two places it
 * "chooses" anything are presentation only, and both are existing app conventions:
 *   - a colour *band* from a given 0-100 score (`scoreBand`, already used by the
 *     dashboard and visit surfaces to colour scores) — the score is the datum, the
 *     band is just which colour paints it;
 *   - the layered topology edges, which are the graph the backend already
 *     specifies through each node's `parent_id` — drawn, not invented.
 */

import { scoreBand, sleLabel, humanizeKey } from '../shared/format';
import type {
  AppendixModel,
  Band,
  ClientsModel,
  EvidenceItem,
  ExecutiveModel,
  Finding,
  HealthModel,
  InventoryModel,
  Recommendation,
  ReportModel,
  ReportSeverity,
  RfModel,
  RssiBin,
  ScopeModel,
  TopoLayer,
  TopoLink,
  TopologyModel,
  WorstDevice,
} from './model';
import type {
  WireFinding,
  WireHistogram,
  WireReport,
  WireTopoNode,
  WireTopology,
} from './wire';

const SEVERITIES = new Set<ReportSeverity>(['critical', 'high', 'low', 'info']);
function asSeverity(s: string | null | undefined): ReportSeverity {
  return s != null && SEVERITIES.has(s as ReportSeverity) ? (s as ReportSeverity) : 'info';
}

function bandOf(score: number | null): Band {
  return scoreBand(score);
}

/** "every 30 s" / "every 5 min" / "every 2 h" from a cadence in seconds. */
function cadence(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s) || s <= 0) return '—';
  if (s < 60) return `every ${Math.round(s)} s`;
  if (s < 3600) return `every ${Math.round(s / 60)} min`;
  if (s < 86_400) return `every ${Math.round(s / 3600)} h`;
  return `every ${Math.round(s / 86_400)} d`;
}

/**
 * Curated evidence whitelist: only these MEASURED keys become chips, each with a
 * label and unit. Thresholds/constants, boolean flags and raw MAC/BSSID fields are
 * deliberately excluded — the Observation sentence already carries the reading, and
 * a chip must be a measurement, never a debug-field dump (a `bad_rssi_dbm` constant
 * is a threshold, not a reading). Order here is the render order, so it is stable.
 */
const EVIDENCE_SPEC: Record<string, { label: string; unit?: string; pct?: boolean }> = {
  median_uplink_rssi: { label: 'Median uplink', unit: 'dBm' },
  median_rssi: { label: 'Median RSSI', unit: 'dBm' },
  better_ap_median_rssi: { label: 'Nearer AP', unit: 'dBm' },
  sustained_fraction_below: { label: 'Below floor', pct: true },
  cu_total: { label: 'Airtime', unit: '%' },
  cu_total_pct: { label: 'Airtime', unit: '%' },
  ports_storming: { label: 'Ports storming' },
  rx_crc_errors: { label: 'RX CRC errors' },
  neighbor_bss_total: { label: 'Neighbour BSS' },
  neighbor_density_issue_count: { label: 'Crowded bands' },
  channel_plan_issue_count: { label: 'Channel contention' },
};

/** Curated, unit-labelled evidence chips — a whitelist, never every scalar key. */
function evidenceItems(evidence: Record<string, unknown>): EvidenceItem[] {
  const out: EvidenceItem[] = [];
  for (const [key, spec] of Object.entries(EVIDENCE_SPEC)) {
    const v = evidence[key];
    if (v == null || (typeof v !== 'number' && typeof v !== 'string')) continue;
    const value =
      spec.pct && typeof v === 'number'
        ? `${Math.round(v * 100)}%`
        : `${v}${spec.unit ? ` ${spec.unit}` : ''}`;
    out.push({ label: spec.label, value });
    if (out.length >= 6) break;
  }
  return out;
}

function backhaulBand(status: string | null): Band | null {
  if (status === 'good') return 'good';
  if (status === 'warn') return 'fair';
  if (status === 'bad') return 'poor';
  return null;
}

/** Worst backhaul first, so a health-flagged mesh AP is never dropped to overflow. */
function backhaulRank(status: string | null): number {
  if (status === 'bad') return 0;
  if (status === 'warn') return 1;
  if (status === 'good') return 2;
  return 3;
}

function nodeId(entityId: number): string {
  return `n${entityId}`;
}

/**
 * The backend omits health-trend buckets with no exposed minutes (a gap is a
 * gap). Since the line chart connects consecutive points, insert an explicit null
 * wherever two points are more than ~1.8 buckets apart, so the gap renders as a
 * discontinuity instead of an interpolated segment. This marks absence; it never
 * invents a score.
 */
function insertGaps(points: { ts: number; value: number | null }[]): { ts: number; value: number | null }[] {
  if (points.length < 3) return points;
  const deltas: number[] = [];
  for (let i = 1; i < points.length; i++) deltas.push(points[i].ts - points[i - 1].ts);
  const step = deltas.slice().sort((a, b) => a - b)[Math.floor(deltas.length / 2)] || 0;
  if (step <= 0) return points;
  const out: { ts: number; value: number | null }[] = [];
  for (let i = 0; i < points.length; i++) {
    if (i > 0 && points[i].ts - points[i - 1].ts > step * 1.8) {
      out.push({ ts: points[i - 1].ts + step, value: null });
    }
    out.push(points[i]);
  }
  return out;
}

function buildTopology(t: WireTopology, clientCount: number): TopologyModel | null {
  const hasDevices = t.gateway != null || t.switches.length > 0 || t.aps.length > 0;
  if (!hasDevices) return null;

  const known = new Set<number>();
  if (t.gateway) known.add(t.gateway.entity_id);
  for (const s of t.switches) known.add(s.entity_id);
  for (const a of t.aps) known.add(a.entity_id);

  const layers: TopoLayer[] = [];
  layers.push({
    kind: 'internet',
    label: 'Internet',
    nodes: [{ id: 'internet', label: 'Internet', sublabel: null, kind: 'internet', badge: null, mesh_uplink: null }],
  });
  if (t.gateway) {
    layers.push({
      kind: 'gateway',
      label: 'Gateway',
      nodes: [
        {
          id: nodeId(t.gateway.entity_id),
          label: t.gateway.name,
          sublabel: t.gateway.model,
          kind: 'gateway',
          badge: null,
          mesh_uplink: null,
        },
      ],
    });
  }
  if (t.switches.length) {
    layers.push({
      kind: 'switch',
      label: 'Switches',
      nodes: t.switches.map((s) => ({
        id: nodeId(s.entity_id),
        label: s.name,
        sublabel: s.model,
        kind: 'switch' as const,
        badge: null,
        mesh_uplink: null,
      })),
    });
  }
  // Wired-uplink APs and mesh (wireless-uplink) APs get their own layers, matching
  // the spec's internet → … → APs → mesh → clients order. Splitting them means a
  // mesh AP with an open finding sits in its own (smaller) row and is never pushed
  // into the '+N more' overflow of a crowded APs row.
  const wiredAps = t.aps.filter((a) => a.uplink !== 'wireless');
  const meshAps = t.aps.filter((a) => a.uplink === 'wireless');
  if (wiredAps.length) {
    layers.push({
      kind: 'ap',
      label: 'Access points',
      nodes: wiredAps.map((a) => ({
        id: nodeId(a.entity_id),
        label: a.name,
        sublabel: a.model,
        kind: 'ap' as const,
        badge: a.client_count > 0 ? String(a.client_count) : null,
        mesh_uplink: null,
      })),
    });
  }
  if (meshAps.length) {
    const ordered = meshAps
      .slice()
      .sort((a, b) => backhaulRank(a.backhaul_status) - backhaulRank(b.backhaul_status));
    layers.push({
      kind: 'mesh',
      label: 'Mesh access points',
      nodes: ordered.map((a) => ({
        id: nodeId(a.entity_id),
        label: a.name,
        sublabel: a.model,
        kind: 'mesh' as const,
        badge: a.client_count > 0 ? String(a.client_count) : null,
        mesh_uplink: {
          rssi: a.mesh_uplink_rssi != null ? Math.round(a.mesh_uplink_rssi) : null,
          health: backhaulBand(a.backhaul_status),
        },
      })),
    });
  }
  if (clientCount > 0) {
    layers.push({
      kind: 'client',
      label: 'Clients',
      nodes: [
        { id: 'clients', label: `${clientCount} clients`, sublabel: null, kind: 'client', badge: null, mesh_uplink: null },
      ],
    });
  }

  const links: TopoLink[] = [];
  if (t.gateway) {
    links.push({ from: 'internet', to: nodeId(t.gateway.entity_id), kind: 'wired', rssi: null, health: null, label: null });
  }
  // Wired uplinks to a known parent. A mesh AP's parent is often not reported by
  // the controller (parent_id null); its backhaul health/RSSI is shown on the node
  // instead of inventing an edge to a guessed parent. When a parent IS reported,
  // the mesh backhaul is drawn as an explicit health-coloured, RSSI-labelled edge.
  const linkUp = (n: WireTopoNode) => {
    if (n.uplink === 'wireless' || n.parent_id == null || !known.has(n.parent_id)) return;
    links.push({ from: nodeId(n.parent_id), to: nodeId(n.entity_id), kind: 'wired', rssi: null, health: null, label: null });
  };
  for (const s of t.switches) linkUp(s);
  for (const a of wiredAps) linkUp(a);
  for (const a of meshAps) {
    if (a.parent_id != null && known.has(a.parent_id)) {
      links.push({
        from: nodeId(a.parent_id),
        to: nodeId(a.entity_id),
        kind: 'mesh',
        rssi: a.mesh_uplink_rssi != null ? Math.round(a.mesh_uplink_rssi) : null,
        health: backhaulBand(a.backhaul_status),
        label: a.mesh_uplink_rssi != null ? `${Math.round(a.mesh_uplink_rssi)} dBm` : null,
      });
    }
  }
  if (clientCount > 0) {
    for (const a of t.aps) {
      if (a.client_count > 0) {
        links.push({ from: nodeId(a.entity_id), to: 'clients', kind: 'wired', rssi: null, health: null, label: null });
      }
    }
  }

  return { layers, links, note: null };
}

function binLabel(floor: number | null, ceil: number | null): string {
  if (floor == null && ceil != null) return `<${ceil}`;
  if (ceil == null && floor != null) return `≥${floor}`;
  if (ceil != null) return String(ceil);
  return '—';
}

function rssiBins(h: WireHistogram): RssiBin[] {
  return h.bins.map((b) => ({
    label: binLabel(b.floor, b.ceil),
    min_dbm: b.floor ?? (b.ceil != null ? b.ceil - 5 : -100),
    max_dbm: b.ceil ?? (b.floor != null ? b.floor + 5 : -30),
    count: b.count,
    weak: b.weak,
  }));
}

function worstClient(w: WireReport['clients']['worst_devices'][number]): WorstDevice {
  // These are the worst-experiencing CLIENTS. What explains a client's ranking is
  // the disconnect/roam churn it saw and the issues open on it — failed SLE minutes
  // attribute to infrastructure, not the client, so the composite and fail-minutes
  // are no longer the same number rendered twice. Show the meaningful channels.
  //
  // `fail_minutes` is deliberately NOT one of them. On this surface it is the
  // minutes attributed *to* the client, and nothing is ever attributed to a
  // client — so it is structurally 0, and a row reading "Failed minutes 0" would
  // say this client lost nothing when in fact its lost minutes are counted
  // against its AP (Gitea #38). Silence beats a true number that reads false.
  const metrics: EvidenceItem[] = [];
  if (w.event_count > 0) metrics.push({ label: 'Disconnects/roams', value: String(w.event_count) });
  const issues =
    w.issue_counts.total ?? (w.issue_counts.p1 ?? 0) + (w.issue_counts.p2 ?? 0) + (w.issue_counts.p3 ?? 0);
  if (issues > 0) metrics.push({ label: 'Open issues', value: String(issues) });
  if (metrics.length === 0) metrics.push({ label: 'Burden score', value: String(w.score) });
  return { name: w.entity?.name ?? 'unknown', metrics };
}

function finding(f: WireFinding): Finding {
  return {
    id: f.id,
    title: f.title,
    severity: asSeverity(f.severity),
    affected: f.affected_assets.map((a) => a.name),
    observation: f.observation,
    evidence: evidenceItems(f.evidence),
    evidence_chart: null,
    impact: f.impact.summary,
    root_cause: f.root_cause,
    recommendation: f.recommendation ? [f.recommendation] : [],
    incident_id: f.incident_id,
  };
}

export function fromWire(w: WireReport): ReportModel {
  const executive: ExecutiveModel = {
    verdict: w.executive_summary.verdict,
    overall_score: w.executive_summary.scorecard.health_score,
    band: bandOf(w.executive_summary.scorecard.health_score),
    severity_counts: {
      critical: w.executive_summary.scorecard.findings_by_severity.critical ?? 0,
      high: w.executive_summary.scorecard.findings_by_severity.high ?? 0,
      low: w.executive_summary.scorecard.findings_by_severity.low ?? 0,
      info: w.executive_summary.scorecard.findings_by_severity.info ?? 0,
    },
    top_findings: w.executive_summary.top_findings.map((f) => ({
      id: f.id,
      title: f.title,
      business_impact: f.plain,
      severity: asSeverity(f.severity),
    })),
    recommendation_summary: w.executive_summary.recommendation_summary,
    coverage_pct: w.executive_summary.scorecard.coverage_pct ?? null,
    low_confidence: w.executive_summary.scorecard.low_confidence ?? false,
    confidence_note: w.executive_summary.scorecard.confidence_note ?? null,
  };

  const scope: ScopeModel = {
    data_sources: w.scope.data_sources,
    sampling: w.scope.sampling.map((s) => ({
      source: s.source,
      cadence: s.cadence_s != null ? cadence(s.cadence_s) : s.note,
    })),
    coverage: w.scope.coverage.map((c) => ({
      job: humanizeKey(c.job),
      interval: cadence(c.interval_s),
      fraction: c.fraction,
      note: c.note,
    })),
    limitations: w.scope.limitations,
  };

  const inventory: InventoryModel = {
    counts: {
      aps: w.cover.counts.aps ?? null,
      switches: w.cover.counts.switches ?? null,
      gateways: w.cover.counts.gateways ?? null,
      clients: w.cover.counts.clients ?? null,
    },
    devices: w.inventory.devices.map((d) => ({
      id: d.entity_id,
      name: d.name,
      model: d.model,
      role: humanizeKey(d.role),
      uplink: d.uplink,
    })),
  };

  const health: HealthModel = {
    overall: { score: w.health.headline_score, band: bandOf(w.health.headline_score) },
    trend: {
      points: insertGaps(w.health.trend.map((p) => ({ ts: p.ts, value: p.score as number | null }))),
      summary_stat:
        w.health.headline_score != null ? `${w.health.headline_score} / 100 overall` : null,
      as_of: w.cover.generated_ts,
    },
    sles: w.health.sles.map((s) => ({
      key: s.sle,
      label: sleLabel(s.sle),
      score: s.score,
      band: bandOf(s.score),
      top_offenders: s.top_offenders.map((o) => ({
        name: o.entity?.name ?? 'unattributed',
        fail_minutes: o.fail_minutes,
      })),
      low_confidence: s.low_confidence ?? false,
    })),
  };

  const rf: RfModel = {
    utilization: w.rf.utilization
      .filter((u) => u.band != null && u.channel != null)
      .map((u) => ({
        band: String(u.band),
        channel: u.channel as string | number,
        ap_name: u.ap_name,
        utilization_pct: u.cu_total,
      })),
    neighbors: w.rf.neighbor_density.by_channel
      .filter((n) => n.band != null && n.channel != null)
      .map((n) => ({ band: String(n.band), channel: n.channel as string | number, count: n.count })),
    reference_pct: w.rf.utilization_reference_pct,
    summary: w.rf.neighbor_summary,
    rogue_count: null,
  };

  const clients: ClientsModel = {
    rssi_histogram: rssiBins(w.clients.rssi_histogram),
    per_ap_load: w.clients.clients_per_ap.map((a) => ({ ap_name: a.name, client_count: a.client_count })),
    worst_devices: w.clients.worst_devices.map(worstClient),
    total_clients: w.clients.rssi_histogram.total,
  };

  const recommendations: Recommendation[] = [
    ...w.roadmap.now,
    ...w.roadmap.soon,
    ...w.roadmap.strategic,
  ].map((r) => ({
    finding_id: r.finding_id,
    severity: asSeverity(r.severity),
    phase: r.phase === 'now' || r.phase === 'soon' ? r.phase : 'strategic',
    text: r.text,
  }));

  const appendix: AppendixModel = {
    rubric: w.appendix.severity_rubric.map((r) => ({
      severity: asSeverity(r.level),
      definition: r.meaning,
    })),
    thresholds: Object.entries(w.appendix.thresholds).map(([k, v]) => ({
      name: humanizeKey(k),
      value:
        v != null && typeof v === 'object'
          ? Object.entries(v as Record<string, unknown>)
              .map(([kk, vv]) => `${humanizeKey(kk)} ${vv}`)
              .join(', ')
          : String(v),
      source: null,
    })),
    glossary: w.appendix.glossary.map((g) => ({ term: g.term, definition: g.definition })),
    methodology_detail: Object.values(w.appendix.methodology),
  };

  return {
    meta: {
      title: 'Network Assessment',
      site_name: w.cover.site,
      generated_ts: w.cover.generated_ts,
      window: {
        start_ts: w.cover.window.start_ts,
        end_ts: w.cover.window.end_ts,
        label: w.cover.window.label,
      },
      tool: w.cover.tool,
      tool_version: w.cover.version,
      confidentiality: w.cover.confidentiality,
    },
    executive,
    scope,
    inventory,
    topology: buildTopology(w.topology, w.cover.counts.clients ?? 0),
    health,
    rf,
    clients,
    findings: w.findings.map(finding),
    recommendations,
    appendix,
  };
}
