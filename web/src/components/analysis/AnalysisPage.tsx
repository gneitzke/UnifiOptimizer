import React, {
  useState,
  useEffect,
  useCallback,
} from 'react';
import {
  useParams,
  useNavigate,
} from 'react-router-dom';
import {
  Users,
  Radio,
  AlertTriangle,
  CheckCircle2,
  ArrowUpDown,
  Eye,
  RefreshCw,
  Loader2,
} from 'lucide-react';
import type {
  AnalysisJob,
  AnalysisResult,
  ApAnalysis,
  ClientAnalysis,
  Finding,
  TopologyNode,
  EventTimeline,
  ComponentScores,
  ClientCapabilities,
  ManufacturerStats,
  ClientJourney,
} from '../../types/api';
import * as api from '../../services/api';
import StatCard from '../dashboard/StatCard';

/* ── Tabs ──────────────────────────────────────── */

type Tab =
  | 'overview'
  | 'devices'
  | 'clients'
  | 'channels'
  | 'recommendations';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'devices', label: 'Devices' },
  { key: 'clients', label: 'Clients' },
  { key: 'channels', label: 'Channels' },
  { key: 'recommendations', label: 'Recs' },
];

/* ── Chart Colors ──────────────────────────────── */

const SIGNAL_COLORS = [
  '#00c48f', // Excellent - green
  '#0088ff', // Good - blue
  '#ffb800', // Fair - yellow
  '#ff8c00', // Poor - orange
  '#ff4757', // Critical - red
];

const BAND_COLORS: Record<string, string> = {
  '2.4GHz': '#ffb800',
  '5GHz': '#0088ff',
  '6GHz': '#a855f7',
};

/* ── UniFi Model Friendly Names ────────────────── */
const MODEL_NAMES: Record<string, string> = {
  // Access Points
  U7PG2: 'AC Pro Gen2',
  U7MP: 'AC Mesh Pro',
  U7MSH: 'AC Mesh',
  U7LR: 'AC Long-Range',
  U7LT: 'AC Lite',
  U7P: 'AC Pro',
  U7E: 'AC',
  U7O: 'AC Outdoor',
  U7Ev2: 'AC v2',
  UAL6: 'U6 Lite',
  UALR6: 'U6 Long-Range',
  UAP6: 'U6 Pro',
  UAE6: 'U6 Enterprise',
  UAPA6A9: 'U6 Pro Max',
  U6M: 'U6 Mesh',
  UAM6: 'U6 Mesh',
  U6E: 'U6 Enterprise',
  U6IW: 'U6 In-Wall',
  U6EW: 'U6 Enterprise IW',
  U7IW: 'AC In-Wall',
  U7IWP: 'AC In-Wall Pro',
  // Switches
  US16P150: 'Switch 16 PoE 150W',
  US8P150: 'Switch 8 PoE 150W',
  US8P60: 'Switch 8 PoE 60W',
  US24P250: 'Switch 24 PoE 250W',
  US48P750: 'Switch 48 PoE 750W',
  USMINI: 'Flex Mini',
  USL8LP: 'Lite 8 PoE',
  USL16LP: 'Lite 16 PoE',
  USPPD: 'Switch Pro 24 PoE',
  // Gateways
  UGW3: 'Security Gateway 3P',
  UGW4: 'Security Gateway 4P',
  UDMPRO: 'Dream Machine Pro',
  UDM: 'Dream Machine',
  UDMSE: 'Dream Machine SE',
  UDR: 'Dream Router',
  UXGPRO: 'Next-Gen Gateway Pro',
  UCK: 'Cloud Key',
  UCKP: 'Cloud Key Plus',
  UCKGEN2: 'Cloud Key Gen2',
  UCKGEN2P: 'Cloud Key Gen2+',
};

function friendlyModel(code: string): string {
  return MODEL_NAMES[code] ?? code;
}

/* ── Skeleton ──────────────────────────────────── */

function SkeletonCard() {
  return (
    <div
      className="glass-card-solid p-6 h-32
        animate-pulse"
      style={{
        background: 'var(--bg-elevated)',
      }}
    />
  );
}

/* ── Priority Badge ────────────────────────────── */

function PriorityBadge({
  severity,
}: {
  severity: string;
}) {
  const map: Record<string, string> = {
    critical: 'var(--error)',
    warning: 'var(--warning)',
    info: 'var(--primary)',
  };
  const c = map[severity] ?? 'var(--text-muted)';
  return (
    <span
      className="text-[10px] uppercase
        font-semibold px-2 py-0.5 rounded-full"
      style={{
        background: `color-mix(
          in srgb, ${c} 20%, transparent
        )`,
        color: c,
      }}
    >
      {severity}
    </span>
  );
}

/* ── Health Bar ────────────────────────────────── */

function HealthBar({
  label,
  value,
  max,
}: {
  label: string;
  value: number;
  max?: number;
}) {
  const pct = max ? Math.round((value / max) * 100) : value;
  const color =
    pct >= 80
      ? 'var(--success)'
      : pct >= 50
        ? 'var(--warning)'
        : 'var(--error)';
  return (
    <div className="space-y-1">
      <div className="flex justify-between">
        <span
          className="text-xs"
          style={{ color: 'var(--text-muted)' }}
        >
          {label}
        </span>
        <span
          className="text-xs font-semibold"
          style={{ color }}
        >
          {value}{max ? `/${max}` : ''}
        </span>
      </div>
      <div
        className="h-2 rounded-full overflow-hidden"
        style={{
          background: 'var(--bg-elevated)',
        }}
      >
        <div
          className="h-full rounded-full"
          style={{
            width: `${pct}%`,
            background: color,
            transition: 'width 0.8s ease',
          }}
        />
      </div>
    </div>
  );
}

/* ── Network Topology DAG ──────────────────────── */

const NODE_COLORS: Record<TopologyNode['type'], string> = {
  switch: '#0088ff',
  ap: '#00c48f',
  mesh: '#ffb800',
};

const NODE_ICONS: Record<TopologyNode['type'], string> = {
  switch: '⬡',
  ap: '◉',
  mesh: '◎',
};

function NetworkDAG({ topology }: { topology: TopologyNode[] }) {
  if (topology.length === 0) return null;

  // Build tree structure
  type TreeNode = { node: TopologyNode; children: TreeNode[] };
  const byName = new Map<string, TreeNode>();
  topology.forEach((n) => byName.set(n.name, { node: n, children: [] }));

  const roots: TreeNode[] = [];
  topology.forEach((n) => {
    const tn = byName.get(n.name)!;
    const parent = byName.get(n.parentName);
    if (parent) parent.children.push(tn);
    else roots.push(tn);
  });

  // Sort children by client count desc
  function sortTree(t: TreeNode) {
    t.children.sort((a, b) => b.node.clients - a.node.clients);
    t.children.forEach(sortTree);
  }
  roots.forEach(sortTree);

  // Layout: assign x,y positions using a breadth-first horizontal spread
  type Positioned = { node: TopologyNode; x: number; y: number; children: Positioned[] };
  const nodeW = 120;
  const nodeH = 56;
  const gapX = 24;
  const gapY = 80;

  function layoutTree(tree: TreeNode, depth: number, xOffset: number): { positioned: Positioned; width: number } {
    if (tree.children.length === 0) {
      return { positioned: { node: tree.node, x: xOffset, y: depth * (nodeH + gapY), children: [] }, width: nodeW };
    }
    let childX = xOffset;
    const childPositions: Positioned[] = [];
    let totalWidth = 0;
    tree.children.forEach((child, i) => {
      const { positioned, width } = layoutTree(child, depth + 1, childX);
      childPositions.push(positioned);
      childX += width + (i < tree.children.length - 1 ? gapX : 0);
      totalWidth += width + (i < tree.children.length - 1 ? gapX : 0);
    });
    const parentX = childPositions.length > 0
      ? (childPositions[0].x + childPositions[childPositions.length - 1].x) / 2
      : xOffset;
    return {
      positioned: { node: tree.node, x: parentX, y: depth * (nodeH + gapY), children: childPositions },
      width: Math.max(totalWidth, nodeW),
    };
  }

  let globalX = 0;
  const positionedRoots: Positioned[] = [];
  roots.forEach((root, i) => {
    const { positioned, width } = layoutTree(root, 0, globalX);
    positionedRoots.push(positioned);
    globalX += width + (i < roots.length - 1 ? gapX * 2 : 0);
  });

  // Calculate SVG bounds
  function getBounds(nodes: Positioned[]): { maxX: number; maxY: number } {
    let maxX = 0;
    let maxY = 0;
    nodes.forEach((n) => {
      maxX = Math.max(maxX, n.x + nodeW);
      maxY = Math.max(maxY, n.y + nodeH);
      const childBounds = getBounds(n.children);
      maxX = Math.max(maxX, childBounds.maxX);
      maxY = Math.max(maxY, childBounds.maxY);
    });
    return { maxX, maxY };
  }
  const { maxX, maxY } = getBounds(positionedRoots);
  const svgW = maxX + 20;
  const svgH = maxY + 20;

  function renderEdges(nodes: Positioned[]): React.ReactNode[] {
    const edges: React.ReactNode[] = [];
    nodes.forEach((n) => {
      const px = n.x + nodeW / 2;
      const py = n.y + nodeH;
      n.children.forEach((child) => {
        const cx = child.x + nodeW / 2;
        const cy = child.y;
        const midY = (py + cy) / 2;
        edges.push(
          <path key={`${n.node.name}-${child.node.name}`}
            d={`M${px},${py} C${px},${midY} ${cx},${midY} ${cx},${cy}`}
            fill="none" stroke="var(--border-strong)" strokeWidth="2" />
        );
      });
      edges.push(...renderEdges(n.children));
    });
    return edges;
  }

  function renderNodes(nodes: Positioned[]): React.ReactNode[] {
    const result: React.ReactNode[] = [];
    nodes.forEach((n) => {
      const color = NODE_COLORS[n.node.type];
      const offline = n.node.isOffline;
      const strokeColor = offline ? 'var(--error)' : color;
      const strokeStyle = offline ? '3,3' : 'none';
      result.push(
        <g key={n.node.name} transform={`translate(${n.x},${n.y})`} opacity={offline ? 0.75 : 1}>
          <rect width={nodeW} height={nodeH} rx="10" fill={`${color}15`}
            stroke={strokeColor} strokeWidth="1.5" strokeDasharray={strokeStyle} />
          <text x={nodeW / 2} y={18} textAnchor="middle" fill="var(--text)" fontSize="11" fontWeight="600">
            {n.node.name.length > 14 ? n.node.name.slice(0, 13) + '…' : n.node.name}
          </text>
          <text x={nodeW / 2} y={32} textAnchor="middle" fill="var(--text-muted)" fontSize="9">
            {friendlyModel(n.node.model)}
          </text>
          <text x={nodeW / 2} y={46} textAnchor="middle" fill={offline ? 'var(--error)' : color} fontSize="9" fontWeight="600">
            {offline
              ? '⚠ OFFLINE'
              : `${NODE_ICONS[n.node.type]} ${n.node.type === 'switch' ? 'SWITCH' : n.node.type === 'mesh' ? 'MESH' : 'WIRED'}${n.node.clients > 0 ? ` · ${n.node.clients}` : ''}`
            }
          </text>
        </g>
      );
      result.push(...renderNodes(n.children));
    });
    return result;
  }

  return (
    <div className="glass-card-solid p-6">
      <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--text)' }}>Network Topology</h3>
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${svgW} ${svgH}`} style={{ minWidth: Math.min(svgW, 600), width: '100%', height: 'auto' }}>
          {renderEdges(positionedRoots)}
          {renderNodes(positionedRoots)}
        </svg>
      </div>
      {/* Legend */}
      <div className="flex gap-5 mt-4 pt-3" style={{ borderTop: '1px solid var(--border)' }}>
        {([['switch', 'Switch'], ['ap', 'Wired AP'], ['mesh', 'Mesh AP']] as const).map(([t, label]) => (
          <div key={t} className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-muted)' }}>
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: NODE_COLORS[t] }} />
            {label}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Overview Tab ──────────────────────────────── */

function OverviewTab({
  result,
}: {
  result: AnalysisResult;
}) {
  const { health, aps } = result;
  const sd = result.signalDistribution;

  // Use backend's pre-computed signal distribution
  const signalBuckets = [
    { name: 'Excellent', value: sd.excellent },
    { name: 'Good', value: sd.good },
    { name: 'Fair', value: sd.fair },
    { name: 'Poor', value: sd.poor },
    { name: 'Critical', value: sd.critical },
  ];

  // Count radios across all APs (each AP can have multiple bands)
  const bandCounts: Record<string, number> = {};
  aps.forEach((a) =>
    a.radios.forEach((r) => {
      bandCounts[r.band] = (bandCounts[r.band] ?? 0) + 1;
    }),
  );
  const bandData = Object.entries(bandCounts)
    .map(([name, value]) => ({ name, value }))
    .filter((d) => d.value > 0);

  return (
    <div className="space-y-6">
      {/* Health Breakdown */}
      <div className="glass-card-solid p-6">
        <h3
          className="text-sm font-semibold mb-4"
          style={{ color: 'var(--text)' }}
        >
          Health Score Breakdown
        </h3>
        <div className="space-y-3">
          <HealthBar
            label="RF Quality"
            value={health.wireless}
            max={health.wirelessMax}
          />
          <HealthBar
            label="Mesh / Coverage"
            value={health.coverage}
            max={health.coverageMax}
          />
          <HealthBar
            label="Distribution"
            value={health.wired}
            max={health.wiredMax}
          />
          <HealthBar
            label="Airtime"
            value={health.latency}
            max={health.latencyMax}
          />
          {(health.issuesScore !== undefined) && (
            <HealthBar
              label="Issues"
              value={health.issuesScore}
              max={10}
            />
          )}
        </div>
      </div>

      {/* Network Topology */}
      <NetworkDAG topology={result.topology} />

      {/* Charts Row */}
      <div
        className="grid grid-cols-1 md:grid-cols-2
          gap-4"
      >
        <div className="glass-card-solid p-6">
          <h3
            className="text-sm font-semibold mb-4"
            style={{ color: 'var(--text)' }}
          >
            Signal Distribution
          </h3>
          {signalBuckets.filter((s) => s.value > 0)
            .length > 0 ? (
            <div className="space-y-2">
              {signalBuckets.map((s, i) => {
                if (s.value === 0) return null;
                const total = signalBuckets.reduce(
                  (n, b) => n + b.value, 0,
                );
                const pct = total > 0
                  ? (s.value / total) * 100
                  : 0;
                return (
                  <div
                    key={s.name}
                    className="flex items-center gap-3"
                  >
                    <span
                      className="text-xs w-16
                        shrink-0 font-medium"
                      style={{
                        color: SIGNAL_COLORS[i],
                      }}
                    >
                      {s.name}
                    </span>
                    <div
                      className="flex-1 h-4
                        rounded-full overflow-hidden"
                      style={{
                        background:
                          'var(--bg-elevated)',
                      }}
                    >
                      <div
                        className="h-full
                          rounded-full"
                        style={{
                          width: `${pct}%`,
                          background:
                            SIGNAL_COLORS[i],
                          transition:
                            'width 0.6s ease',
                        }}
                      />
                    </div>
                    <span
                      className="text-xs w-10
                        text-right shrink-0
                        font-semibold"
                      style={{
                        color: 'var(--text)',
                      }}
                    >
                      {s.value}
                    </span>
                  </div>
                );
              })}
              {sd.wired > 0 && (
                <div
                  className="flex items-center gap-3"
                >
                  <span
                    className="text-xs w-16
                      shrink-0 font-medium"
                    style={{
                      color: 'var(--text-muted)',
                    }}
                  >
                    Wired
                  </span>
                  <div
                    className="flex-1 h-4
                      rounded-full overflow-hidden"
                    style={{
                      background:
                        'var(--bg-elevated)',
                    }}
                  >
                    <div
                      className="h-full
                        rounded-full"
                      style={{
                        width: `${(sd.wired / signalBuckets.reduce((n, b) => n + b.value, 0)) * 100}%`,
                        background:
                          'var(--text-muted)',
                        transition:
                          'width 0.6s ease',
                      }}
                    />
                  </div>
                  <span
                    className="text-xs w-10
                      text-right shrink-0
                      font-semibold"
                    style={{
                      color: 'var(--text)',
                    }}
                  >
                    {sd.wired}
                  </span>
                </div>
              )}
            </div>
          ) : (
            <p
              className="text-sm"
              style={{
                color: 'var(--text-muted)',
              }}
            >
              No signal data available
            </p>
          )}
        </div>

        <div className="glass-card-solid p-6">
          <h3
            className="text-sm font-semibold mb-4"
            style={{ color: 'var(--text)' }}
          >
            Band Usage
          </h3>
          {bandData.length > 0 ? (
            <div className="space-y-2">
              {bandData.map((d) => {
                const total = bandData.reduce(
                  (n, b) => n + b.value, 0,
                );
                const pct = total > 0
                  ? (d.value / total) * 100
                  : 0;
                const color =
                  BAND_COLORS[d.name] ??
                  'var(--primary)';
                return (
                  <div
                    key={d.name}
                    className="flex items-center gap-3"
                  >
                    <span
                      className="text-xs w-16
                        shrink-0 font-medium"
                      style={{ color }}
                    >
                      {d.name}
                    </span>
                    <div
                      className="flex-1 h-4
                        rounded-full overflow-hidden"
                      style={{
                        background:
                          'var(--bg-elevated)',
                      }}
                    >
                      <div
                        className="h-full
                          rounded-full"
                        style={{
                          width: `${pct}%`,
                          background: color,
                          transition:
                            'width 0.6s ease',
                        }}
                      />
                    </div>
                    <span
                      className="text-xs w-10
                        text-right shrink-0
                        font-semibold"
                      style={{
                        color: 'var(--text)',
                      }}
                    >
                      {d.value}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <p
              className="text-sm"
              style={{
                color: 'var(--text-muted)',
              }}
            >
              No band data available
            </p>
          )}
        </div>
      </div>

      {/* Stat Cards */}
      <div
        className="grid grid-cols-1
          sm:grid-cols-2 lg:grid-cols-4 gap-4"
      >
        <StatCard
          title="Overall Health"
          value={`${health.overall}/100`}
          subtitle={health.grade ? `Grade ${health.grade} — ${health.status}` : undefined}
          icon={CheckCircle2}
          iconColor={
            health.overall >= 80 ? 'var(--success)'
            : health.overall >= 60 ? 'var(--warning)'
            : 'var(--error)'
          }
        />
        <StatCard
          title="Access Points"
          value={result.apCount}
          icon={Radio}
        />
        <StatCard
          title="Clients"
          value={result.clientCount}
          icon={Users}
        />
        <StatCard
          title="Findings"
          value={result.findings.length}
          icon={AlertTriangle}
          iconColor="var(--warning)"
        />
      </div>

      {/* Component Scores */}
      <ComponentScoresCard scores={result.componentScores} />

      {/* Client Capabilities */}
      <ClientCapabilitiesCard caps={result.clientCapabilities} />

      {/* Manufacturer Breakdown */}
      <ManufacturerCard manufacturers={result.manufacturers} />

      {/* Satisfaction Trend */}
      <SatisfactionTrend timeline={result.timeline} />

      {/* Event Summary */}
      <EventSummary timeline={result.timeline} />

      {/* Per-AP Events */}
      <ApEventsCard timeline={result.timeline} />
    </div>
  );
}

/* ── Component Scores Card ──────────────────────── */

const COMP_LABELS: Record<string, string> = {
  rfHealth: 'RF Health',
  clientHealth: 'Client Health',
  infrastructure: 'Infrastructure',
  security: 'Security',
};

const COMP_COLORS = ['#0088ff', '#00c48f', '#a855f7', '#ff8c00'];

function ComponentScoresCard({ scores }: { scores: ComponentScores }) {
  const entries = Object.entries(scores).filter(([, v]) => v > 0);
  if (entries.length === 0) return null;
  return (
    <div className="glass-card-solid p-6">
      <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--text)' }}>
        Health Components
      </h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {entries.map(([key, val], i) => {
          const color = COMP_COLORS[i % COMP_COLORS.length];
          return (
            <div key={key} className="text-center">
              <div className="relative w-16 h-16 mx-auto mb-2">
                <svg viewBox="0 0 36 36" className="w-full h-full">
                  <circle cx="18" cy="18" r="15.5" fill="none" stroke="var(--bg-elevated)" strokeWidth="3" />
                  <circle cx="18" cy="18" r="15.5" fill="none" stroke={color} strokeWidth="3"
                    strokeLinecap="round" strokeDasharray={`${val * 0.975} 100`}
                    transform="rotate(-90 18 18)" style={{ transition: 'stroke-dasharray 1s ease' }} />
                </svg>
                <span className="absolute inset-0 flex items-center justify-center text-sm font-bold"
                  style={{ color }}>{val}</span>
              </div>
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {COMP_LABELS[key] ?? key}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── Client Capabilities Card ──────────────────── */

const WIFI_COLORS: Record<string, string> = {
  '802.11ax': '#00c48f',
  '802.11ac': '#0088ff',
  '802.11n': '#ffb800',
  'legacy': '#ff4757',
};

function DistBar({ items, colors }: { items: Record<string, number>; colors?: Record<string, string> }) {
  const total = Object.values(items).reduce((a, b) => a + b, 0);
  if (total === 0) return null;
  return (
    <div className="space-y-2">
      {Object.entries(items).filter(([, v]) => v > 0).sort(([, a], [, b]) => b - a).map(([name, val]) => {
        const pct = (val / total) * 100;
        const color = colors?.[name] ?? 'var(--primary)';
        return (
          <div key={name} className="flex items-center gap-3">
            <span className="text-xs w-16 shrink-0 font-medium" style={{ color }}>{name}</span>
            <div className="flex-1 h-4 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
              <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color, transition: 'width 0.6s ease' }} />
            </div>
            <span className="text-xs w-8 text-right shrink-0 font-semibold" style={{ color: 'var(--text)' }}>{val}</span>
          </div>
        );
      })}
    </div>
  );
}

function ClientCapabilitiesCard({ caps }: { caps: ClientCapabilities }) {
  const hasStandards = Object.values(caps.wifiStandards).some((v) => v > 0);
  const hasWidths = Object.values(caps.channelWidths).some((v) => v > 0);
  const hasStreams = Object.values(caps.spatialStreams).some((v) => v > 0);
  if (!hasStandards && !hasWidths && !hasStreams) return null;

  return (
    <div className="glass-card-solid p-6">
      <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--text)' }}>Client Capabilities</h3>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {hasStandards && (
          <div>
            <h4 className="text-xs font-medium mb-3" style={{ color: 'var(--text-muted)' }}>WiFi Standard</h4>
            <DistBar items={caps.wifiStandards} colors={WIFI_COLORS} />
          </div>
        )}
        {hasWidths && (
          <div>
            <h4 className="text-xs font-medium mb-3" style={{ color: 'var(--text-muted)' }}>Channel Width</h4>
            <DistBar items={caps.channelWidths} />
          </div>
        )}
        {hasStreams && (
          <div>
            <h4 className="text-xs font-medium mb-3" style={{ color: 'var(--text-muted)' }}>Spatial Streams</h4>
            <DistBar items={caps.spatialStreams} />
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Manufacturer Card ─────────────────────────── */

function ManufacturerCard({ manufacturers }: { manufacturers: ManufacturerStats[] }) {
  if (manufacturers.length === 0) return null;
  const sorted = [...manufacturers].sort((a, b) => b.count - a.count);
  const max = sorted[0]?.count ?? 1;
  return (
    <div className="glass-card-solid p-6">
      <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--text)' }}>Device Manufacturers</h3>
      <div className="space-y-2">
        {sorted.map((m) => (
          <div key={m.name} className="flex items-center gap-3">
            <span className="text-sm shrink-0">{m.icon || '📡'}</span>
            <span className="text-xs w-20 shrink-0 font-medium truncate" style={{ color: 'var(--text)' }}>{m.name}</span>
            <div className="flex-1 h-4 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
              <div className="h-full rounded-full" style={{
                width: `${(m.count / max) * 100}%`,
                background: 'var(--primary)',
                transition: 'width 0.6s ease',
              }} />
            </div>
            <span className="text-xs w-8 text-right shrink-0 font-semibold" style={{ color: 'var(--text)' }}>{m.count}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0" style={{
              background: 'rgba(0,136,255,0.1)', color: 'var(--text-muted)',
            }}>{m.type || 'unknown'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Satisfaction Trend ────────────────────────── */

function SatisfactionTrend({ timeline }: { timeline: EventTimeline }) {
  const entries = Object.entries(timeline.satisfactionByHour);
  if (entries.length === 0) return null;

  // Downsample to ~50 points for display
  const step = Math.max(1, Math.floor(entries.length / 50));
  const sampled = entries.filter((_, i) => i % step === 0 || i === entries.length - 1);
  if (sampled.length < 2) return null;
  const values = sampled.map(([, v]) => v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  // SVG sparkline
  const w = 600;
  const h = 100;
  const points = sampled.map(([, v], i) => {
    const x = (i / (sampled.length - 1)) * w;
    const y = h - ((v - min) / range) * (h - 10) - 5;
    return `${x},${y}`;
  }).join(' ');

  const firstDate = sampled[0]?.[0]?.split(' ')[0] ?? '';
  const lastDate = sampled[sampled.length - 1]?.[0]?.split(' ')[0] ?? '';
  const avgSat = (values.reduce((a, b) => a + b, 0) / values.length).toFixed(1);

  return (
    <div className="glass-card-solid p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
          Satisfaction Trend
        </h3>
        <span className="text-xs font-medium px-2 py-1 rounded" style={{
          background: 'rgba(0,196,143,0.1)', color: 'var(--success)',
        }}>Avg: {avgSat}%</span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full" style={{ height: 120 }}>
        <polyline fill="none" stroke="var(--primary)" strokeWidth="2" points={points} />
        {/* Fill area */}
        <polygon fill="rgba(0,136,255,0.08)" points={`0,${h} ${points} ${w},${h}`} />
      </svg>
      <div className="flex justify-between text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
        <span>{firstDate}</span>
        <span>{lastDate}</span>
      </div>
    </div>
  );
}

/* ── Event Summary ─────────────────────────────── */

const EVENT_COLORS: Record<string, string> = {
  roaming: '#0088ff',
  device_offline: '#ff4757',
  device_restart: '#ff8c00',
  dfs_radar: '#a855f7',
  wifi_quality: '#ffb800',
};

const EVENT_LABELS: Record<string, string> = {
  roaming: 'Roaming',
  device_offline: 'Device Offline',
  device_restart: 'Device Restart',
  dfs_radar: 'DFS Radar',
  wifi_quality: 'WiFi Quality',
};

function EventSummary({ timeline }: { timeline: EventTimeline }) {
  const entries = Object.entries(timeline.totals).filter(([, v]) => v > 0).sort(([, a], [, b]) => b - a);

  // Build density timeline data — one row per event category
  const catEntries = Object.entries(timeline.categories)
    .filter(([k]) => EVENT_COLORS[k])
    .filter(([, arr]) => arr.some((v) => v > 0));
  const hours = timeline.hours;
  const hasDensity = catEntries.length > 0 && hours.length > 1;

  const firstDate = hours[0]?.split(' ')[0] ?? '';
  const lastDate = hours[hours.length - 1]?.split(' ')[0] ?? '';

  // Hover state for tooltip — hooks must be called unconditionally
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number; containerWidth: number }>({ x: 0, y: 0, containerWidth: 400 });
  const containerRef = React.useRef<HTMLDivElement>(null);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const pct = x / rect.width;
    const idx = Math.min(Math.max(Math.floor(pct * hours.length), 0), hours.length - 1);
    setHoverIdx(idx);
    const containerRect = containerRef.current?.getBoundingClientRect();
    if (containerRect) {
      setTooltipPos({ x: e.clientX - containerRect.left, y: e.clientY - containerRect.top, containerWidth: containerRect.width });
    }
  }, [hours.length]);

  const handleMouseLeave = useCallback(() => setHoverIdx(null), []);

  if (entries.length === 0) return null;
  const max = entries[0]?.[1] ?? 1;

  // Build tooltip data for hovered hour
  const tooltipData = hoverIdx !== null ? {
    hour: hours[hoverIdx] ?? '',
    events: catEntries.map(([name, counts]) => ({
      name,
      label: EVENT_LABELS[name] ?? name,
      color: EVENT_COLORS[name] ?? 'var(--primary)',
      count: counts[hoverIdx] ?? 0,
    })).filter((e) => e.count > 0),
    total: catEntries.reduce((sum, [, counts]) => sum + (counts[hoverIdx] ?? 0), 0),
  } : null;

  return (
    <div className="glass-card-solid p-6" ref={containerRef} style={{ position: 'relative' }}>
      <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--text)' }}>
        Event Timeline {timeline.lookbackDays > 0 && <span className="font-normal text-xs" style={{ color: 'var(--text-muted)' }}>({timeline.lookbackDays}d lookback)</span>}
      </h3>

      {/* Density timeline — colored ticks at each hour */}
      {hasDensity && (
        <div className="mb-5" onMouseMove={handleMouseMove} onMouseLeave={handleMouseLeave}
          style={{ cursor: 'crosshair' }}>
          {catEntries.map(([name, counts]) => {
            const catMax = Math.max(...counts);
            if (catMax === 0) return null;
            const color = EVENT_COLORS[name] ?? 'var(--primary)';
            const total = timeline.totals[name] ?? 0;
            return (
              <div key={name} className="mb-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium" style={{ color }}>{EVENT_LABELS[name] ?? name}</span>
                  <span className="text-[10px] font-semibold" style={{ color: 'var(--text-muted)' }}>
                    {total >= 1000 ? `${(total / 1000).toFixed(1)}k` : total} total
                  </span>
                </div>
                <svg viewBox={`0 0 ${counts.length} 20`} preserveAspectRatio="none"
                  className="w-full rounded" style={{ height: 20, background: 'var(--bg-elevated)' }}>
                  {counts.map((v, i) => {
                    if (v === 0) return null;
                    const opacity = Math.min(0.15 + (v / catMax) * 0.85, 1);
                    const h = Math.max(4, (v / catMax) * 20);
                    return (
                      <rect key={i} x={i} y={20 - h} width={1} height={h}
                        fill={color} opacity={opacity} />
                    );
                  })}
                  {/* Hover indicator line */}
                  {hoverIdx !== null && (
                    <rect x={hoverIdx} y={0} width={1} height={20}
                      fill="var(--text)" opacity={0.4} />
                  )}
                </svg>
              </div>
            );
          })}
          {/* Date range labels */}
          <div className="flex justify-between text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
            <span>{firstDate}</span>
            <span>{lastDate}</span>
          </div>
        </div>
      )}

      {/* Hover tooltip */}
      {tooltipData && tooltipData.hour && (
        <div style={{
          position: 'absolute',
          left: Math.min(tooltipPos.x + 12, (tooltipPos.containerWidth || 400) - 180),
          top: tooltipPos.y - 10,
          pointerEvents: 'none',
          zIndex: 50,
        }}>
          <div className="rounded-lg px-3 py-2 shadow-lg" style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border)',
            backdropFilter: 'blur(12px)',
            minWidth: 150,
          }}>
            <div className="text-[10px] font-semibold mb-1.5" style={{ color: 'var(--text-muted)' }}>
              {tooltipData.hour}
            </div>
            {tooltipData.events.length > 0 ? (
              <div className="space-y-1">
                {tooltipData.events.map((e) => (
                  <div key={e.name} className="flex items-center justify-between gap-4">
                    <div className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-sm shrink-0" style={{ background: e.color }} />
                      <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{e.label}</span>
                    </div>
                    <span className="text-[11px] font-semibold tabular-nums" style={{ color: 'var(--text)' }}>{e.count}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>No events</div>
            )}
            {tooltipData.total > 0 && tooltipData.events.length > 1 && (
              <div className="flex justify-between mt-1.5 pt-1.5" style={{ borderTop: '1px solid var(--border)' }}>
                <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>Total</span>
                <span className="text-[10px] font-bold" style={{ color: 'var(--text)' }}>{tooltipData.total}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Totals summary bars */}
      <div className="space-y-2">
        {entries.map(([name, val]) => {
          const color = EVENT_COLORS[name] ?? 'var(--primary)';
          return (
            <div key={name} className="flex items-center gap-3">
              <span className="text-xs w-28 shrink-0 font-medium" style={{ color }}>{EVENT_LABELS[name] ?? name}</span>
              <div className="flex-1 h-4 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
                <div className="h-full rounded-full" style={{
                  width: `${(val / max) * 100}%`, background: color, transition: 'width 0.6s ease',
                }} />
              </div>
              <span className="text-xs w-14 text-right shrink-0 font-semibold" style={{ color: 'var(--text)' }}>
                {val >= 1000 ? `${(val / 1000).toFixed(1)}k` : val}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── Per-AP Events Card ────────────────────────── */

function ApEventsCard({ timeline }: { timeline: EventTimeline }) {
  const apEntries = Object.entries(timeline.apEvents);
  if (apEntries.length === 0) return null;

  // Filter out non-event keys (e.g. restart_uptime, session_restarts)
  const eventKeys = new Set(Object.keys(EVENT_COLORS));

  // Sort by total events descending
  const sorted = apEntries.map(([name, events]) => {
    const filtered = Object.fromEntries(
      Object.entries(events).filter(([k]) => eventKeys.has(k)),
    );
    return {
      name,
      events: filtered,
      total: Object.values(filtered).reduce((a, b) => a + b, 0),
    };
  }).filter((a) => a.total > 0).sort((a, b) => b.total - a.total);

  const max = sorted[0]?.total ?? 1;

  return (
    <div className="glass-card-solid p-6">
      <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--text)' }}>Events by Access Point</h3>
      <div className="space-y-3">
        {sorted.map((ap) => (
          <div key={ap.name}>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium" style={{ color: 'var(--text)' }}>{ap.name}</span>
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {ap.total >= 1000 ? `${(ap.total / 1000).toFixed(1)}k` : ap.total} events
              </span>
            </div>
            {/* Stacked bar */}
            <div className="h-4 rounded-full overflow-hidden flex" style={{ background: 'var(--bg-elevated)', width: `${Math.max((ap.total / max) * 100, 15)}%` }}>
              {Object.entries(ap.events).filter(([, v]) => v > 0).sort(([, a], [, b]) => b - a).map(([evtName, evtVal]) => (
                <div key={evtName} style={{
                  width: `${(evtVal / ap.total) * 100}%`,
                  background: EVENT_COLORS[evtName] ?? 'var(--primary)',
                  minWidth: 2,
                }} title={`${EVENT_LABELS[evtName] ?? evtName}: ${evtVal}`} />
              ))}
            </div>
          </div>
        ))}
      </div>
      {/* Legend */}
      <div className="flex flex-wrap gap-4 mt-4 pt-3" style={{ borderTop: '1px solid var(--border)' }}>
        {Object.entries(EVENT_COLORS).map(([key, color]) => (
          <div key={key} className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-muted)' }}>
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: color }} />
            {EVENT_LABELS[key] ?? key}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Devices Tab ───────────────────────────────── */

type SortField =
  | 'name'
  | 'model'
  | 'type'
  | 'bands'
  | 'clients'
  | 'satisfaction';

function DevicesTab({
  aps,
}: {
  aps: ApAnalysis[];
}) {
  const [sortBy, setSortBy] =
    useState<SortField>('name');
  const [asc, setAsc] = useState(true);

  function handleSort(f: string) {
    const valid: SortField[] = ['name', 'model', 'type', 'bands', 'clients', 'satisfaction'];
    if (valid.includes(f as SortField)) {
      const sf = f as SortField;
      if (sortBy === sf) setAsc(!asc);
      else {
        setSortBy(sf);
        setAsc(true);
      }
    }
  }

  const sorted = [...aps].sort((a, b) => {
    const dir = asc ? 1 : -1;
    if (sortBy === 'name' || sortBy === 'model')
      return dir * a[sortBy].localeCompare(b[sortBy]);
    if (sortBy === 'type') {
      const aType = a.isMesh ? 'mesh' : 'wired';
      const bType = b.isMesh ? 'mesh' : 'wired';
      return dir * aType.localeCompare(bType);
    }
    if (sortBy === 'bands')
      return dir * (a.radios.length - b.radios.length);
    return dir * (a[sortBy] - b[sortBy]);
  });

  const thStyle = {
    color: 'var(--text-muted)',
    borderBottom: '1px solid var(--border)',
  };

  return (
    <div
      className="glass-card-solid overflow-x-auto"
    >
      <table className="w-full text-sm">
        <thead>
          <tr>
            {(
              [
                ['name', 'Name'],
                ['model', 'Model'],
                ['type', 'Type'],
                ['bands', 'Bands'],
                ['clients', 'Clients'],
              ] as const
            ).map(([key, label]) => (
              <th
                key={key}
                className="text-left px-4 py-3
                  text-xs font-medium cursor-pointer
                  select-none"
                style={thStyle}
                onClick={() =>
                  handleSort(key)
                }
              >
                <span className="inline-flex
                  items-center gap-1"
                >
                  {label}
                  {sortBy === key && (
                    <ArrowUpDown size={12} />
                  )}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((ap) => (
            <tr
              key={ap.mac}
              className="hover:opacity-80
                transition-opacity"
              style={{
                borderBottom:
                  '1px solid var(--border)',
              }}
            >
              <td
                className="px-4 py-3 font-medium"
                style={{ color: 'var(--text)' }}
              >
                {ap.name}
              </td>
              <td
                className="px-4 py-3"
                style={{
                  color: 'var(--text-muted)',
                }}
              >
                {friendlyModel(ap.model)}
              </td>
              <td className="px-4 py-3">
                <div className="flex items-center gap-1.5">
                  <span
                    className="text-[10px]
                      font-semibold px-2 py-0.5
                      rounded-full uppercase"
                    style={{
                      background: ap.isMesh
                        ? 'rgba(168,85,247,0.15)'
                        : 'rgba(0,196,143,0.15)',
                      color: ap.isMesh
                        ? '#a855f7'
                        : '#00c48f',
                    }}
                  >
                    {ap.isMesh ? 'Mesh' : 'Wired'}
                  </span>
                  {ap.isOffline && (
                    <span
                      className="text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase"
                      style={{ background: 'rgba(255,71,87,0.15)', color: '#ff4757' }}>
                      Offline
                    </span>
                  )}
                </div>
              </td>
              <td className="px-4 py-3">
                <div className="flex flex-col gap-1">
                  {ap.radios.map((r) => (
                    <div
                      key={r.band}
                      className="flex items-center
                        gap-2 text-xs"
                      style={{
                        color: 'var(--text-muted)',
                      }}
                    >
                      <span
                        className="text-[10px]
                          font-semibold px-1.5
                          py-0.5 rounded"
                        style={{
                          background:
                            BAND_COLORS[r.band]
                              ? `${BAND_COLORS[r.band]}22`
                              : 'rgba(0,136,255,0.1)',
                          color:
                            BAND_COLORS[r.band] ??
                            'var(--primary)',
                        }}
                      >
                        {r.band}
                      </span>
                      <span>
                        ch{r.channel}{r.isAuto ? ' (Auto)' : ''}
                      </span>
                      <span>
                        {r.width}MHz
                      </span>
                      <span>
                        {r.txPowerMode ? `${r.txPowerMode} (${r.txPower}dBm)` : `${r.txPower}dBm`}
                      </span>
                    </div>
                  ))}
                </div>
              </td>
              <td
                className="px-4 py-3"
                style={{
                  color: 'var(--text-muted)',
                }}
              >
                {ap.clients}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Clients Tab ───────────────────────────────── */

const BEHAVIOR_COLORS: Record<string, string> = {
  stable: 'var(--success)',
  healthy_roamer: '#0088ff',
  pattern: '#ffb800',
  flapping: 'var(--error)',
};

const BEHAVIOR_LABELS: Record<string, string> = {
  stable: 'Stable',
  healthy_roamer: 'Roamer',
  pattern: 'Pattern',
  flapping: 'Flapping',
};

function ClientsTab({
  clients,
  journeys,
  signalDistribution,
  clientCapabilities,
  manufacturers,
  totalClients,
}: {
  clients: ClientAnalysis[];
  journeys: ClientJourney[];
  signalDistribution: SignalDistribution;
  clientCapabilities: ClientCapabilities;
  manufacturers: ManufacturerStats[];
  totalClients: number;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  type ClientSort = 'name' | 'ap' | 'roams' | 'rssi' | 'behavior' | 'aps';
  const [sortBy, setSortBy] = useState<ClientSort>('roams');
  const [sortAsc, setSortAsc] = useState(false);
  const [showAll, setShowAll] = useState(false);

  function handleSort(col: ClientSort) {
    if (sortBy === col) setSortAsc(!sortAsc);
    else { setSortBy(col); setSortAsc(col === 'name' || col === 'ap'); }
  }

  const BEHAVIOR_ORDER: Record<string, number> = { flapping: 0, pattern: 1, healthy_roamer: 2, stable: 3 };

  const active = journeys.filter((j) => j.roamCount > 0 || j.disconnectCount > 0);
  const display = showAll ? journeys : active;
  const sorted = [...display]
    .sort((a, b) => {
      const dir = sortAsc ? 1 : -1;
      switch (sortBy) {
        case 'name': return dir * (a.hostname || a.mac).localeCompare(b.hostname || b.mac);
        case 'ap': return dir * a.currentAp.localeCompare(b.currentAp);
        case 'roams': return dir * (a.roamCount - b.roamCount);
        case 'rssi': return dir * (a.currentRssi - b.currentRssi);
        case 'behavior': return dir * ((BEHAVIOR_ORDER[a.behavior] ?? 9) - (BEHAVIOR_ORDER[b.behavior] ?? 9));
        case 'aps': return dir * (a.visitedAps.length - b.visitedAps.length);
        default: return 0;
      }
    });

  const weakClients = clients.filter((c) => c.issues.length > 0);

  const columns: { key: ClientSort; label: string }[] = [
    { key: 'name', label: 'Client' },
    { key: 'ap', label: 'AP' },
    { key: 'roams', label: 'Roams' },
    { key: 'rssi', label: 'Signal' },
    { key: 'behavior', label: 'Behavior' },
    { key: 'aps', label: 'APs Visited' },
  ];

  return (
    <div className="space-y-6">
      {/* Client Summary Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="glass-card-solid p-4 text-center">
          <div className="text-2xl font-bold" style={{ color: 'var(--text)' }}>{totalClients}</div>
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Total Clients</div>
        </div>
        <div className="glass-card-solid p-4 text-center">
          <div className="text-2xl font-bold" style={{ color: 'var(--success)' }}>{signalDistribution.excellent + signalDistribution.good}</div>
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Good+ Signal</div>
        </div>
        <div className="glass-card-solid p-4 text-center">
          <div className="text-2xl font-bold" style={{ color: active.length > 0 ? '#0088ff' : 'var(--text-muted)' }}>{active.length}</div>
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Active Roamers</div>
        </div>
        <div className="glass-card-solid p-4 text-center">
          <div className="text-2xl font-bold" style={{ color: signalDistribution.poor + signalDistribution.critical > 0 ? 'var(--error)' : 'var(--text-muted)' }}>
            {signalDistribution.poor + signalDistribution.critical}
          </div>
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>Poor/Critical Signal</div>
        </div>
      </div>

      {/* Signal Distribution + Capabilities Row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="glass-card-solid p-6">
          <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--text)' }}>Signal Distribution</h3>
          <div className="space-y-2">
            {[
              { name: 'Excellent', value: signalDistribution.excellent, color: '#00c48f' },
              { name: 'Good', value: signalDistribution.good, color: '#0088ff' },
              { name: 'Fair', value: signalDistribution.fair, color: '#ffb800' },
              { name: 'Poor', value: signalDistribution.poor, color: '#ff8c00' },
              { name: 'Critical', value: signalDistribution.critical, color: '#ff4757' },
              { name: 'Wired', value: signalDistribution.wired, color: '#a855f7' },
            ].filter(s => s.value > 0).map((s) => {
              const total = totalClients || 1;
              const pct = (s.value / total) * 100;
              return (
                <div key={s.name} className="flex items-center gap-3">
                  <span className="text-xs w-16 shrink-0 font-medium" style={{ color: s.color }}>{s.name}</span>
                  <div className="flex-1 h-4 rounded-full overflow-hidden" style={{ background: 'var(--bg-elevated)' }}>
                    <div className="h-full rounded-full" style={{ width: `${pct}%`, background: s.color, transition: 'width 0.6s ease' }} />
                  </div>
                  <span className="text-xs w-8 text-right shrink-0 font-semibold" style={{ color: 'var(--text)' }}>{s.value}</span>
                </div>
              );
            })}
          </div>
        </div>
        <ClientCapabilitiesCard caps={clientCapabilities} />
      </div>

      {/* Manufacturer Breakdown */}
      {manufacturers.length > 0 && (
        <div className="glass-card-solid p-6">
          <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--text)' }}>
            Manufacturers ({manufacturers.length} vendors)
          </h3>
          <div className="flex flex-wrap gap-2">
            {manufacturers.sort((a, b) => b.count - a.count).map((m) => (
              <span key={m.name} className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full"
                style={{ background: 'var(--bg-elevated)', color: 'var(--text)' }}>
                {m.icon && <span>{m.icon}</span>}
                {m.name}
                <span className="font-bold" style={{ color: 'var(--primary)' }}>{m.count}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Client Activity Table */}
      {journeys.length > 0 && (
        <div className="glass-card-solid overflow-x-auto">
          <div className="p-4 pb-0 flex items-center justify-between">
            <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
              Client Activity ({sorted.length}{showAll ? ' total' : ' active'})
            </h3>
            <button
              onClick={() => setShowAll(!showAll)}
              className="text-xs font-medium px-3 py-1 rounded-lg cursor-pointer transition-colors"
              style={{ background: 'var(--bg-elevated)', color: 'var(--primary)', border: '1px solid var(--border)' }}>
              {showAll ? `Show Active (${active.length})` : `Show All (${journeys.length})`}
            </button>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr>
                {columns.map((col) => (
                  <th key={col.key}
                    className="text-left px-4 py-3 text-xs font-medium cursor-pointer select-none"
                    style={{ color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}
                    onClick={() => handleSort(col.key)}>
                    <span className="inline-flex items-center gap-1">
                      {col.label}
                      {sortBy === col.key && <ArrowUpDown size={12} />}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((j) => {
                const isExpanded = expanded === j.mac;
                const behaviorColor = BEHAVIOR_COLORS[j.behavior] ?? 'var(--text-muted)';
                return (
                  <React.Fragment key={j.mac}>
                    <tr
                      className="cursor-pointer hover:opacity-80 transition-opacity"
                      style={{ borderBottom: '1px solid var(--border)' }}
                      onClick={() => setExpanded(isExpanded ? null : j.mac)}>
                      <td className="px-4 py-3 font-medium" style={{ color: 'var(--text)' }}>
                        {j.hostname || j.mac.slice(-8)}
                      </td>
                      <td className="px-4 py-3" style={{ color: 'var(--text-muted)' }}>{j.currentAp}</td>
                      <td className="px-4 py-3 font-semibold" style={{
                        color: j.roamCount > 100 ? 'var(--warning)' : j.roamCount > 30 ? '#0088ff' : 'var(--text-muted)',
                      }}>{j.roamCount}</td>
                      <td className="px-4 py-3" style={{
                        color: j.currentRssi >= -50 ? 'var(--success)' : j.currentRssi >= -70 ? 'var(--warning)' : 'var(--error)',
                      }}>{j.currentRssi} dBm</td>
                      <td className="px-4 py-3">
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase"
                          style={{ background: `color-mix(in srgb, ${behaviorColor} 20%, transparent)`, color: behaviorColor }}>
                          {BEHAVIOR_LABELS[j.behavior] ?? j.behavior}
                        </span>
                      </td>
                      <td className="px-4 py-3" style={{ color: 'var(--text-muted)' }}>{j.visitedAps.length}</td>
                    </tr>
                    {isExpanded && j.apPath.length > 0 && (
                      <tr key={`${j.mac}-detail`}>
                        <td colSpan={6} className="px-4 py-3" style={{ background: 'var(--bg-elevated)' }}>
                          <div className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>
                            {j.behaviorDetail}
                          </div>
                          <div className="text-xs font-medium mb-2" style={{ color: 'var(--text)' }}>
                            Visited: {j.visitedAps.join(' → ')}
                          </div>
                          <div className="text-[10px] mb-1" style={{ color: 'var(--text-muted)' }}>Recent roam path:</div>
                          <div className="flex gap-0.5 flex-wrap">
                            {j.apPath.slice(-30).map((p, i) => {
                              const apNames = j.visitedAps;
                              const idx = apNames.indexOf(p.toAp);
                              const colors = ['#0088ff', '#00c48f', '#ff8c00', '#a855f7', '#ff4757', '#ffb800', '#06b6d4', '#ec4899', '#84cc16'];
                              const color = colors[idx >= 0 ? idx % colors.length : 0];
                              return (
                                <div key={i} className="flex flex-col items-center" title={`${p.fromAp} → ${p.toAp} ch${p.channelFrom}→${p.channelTo}`}>
                                  <div className="w-5 h-5 rounded" style={{ background: `${color}30`, border: `1px solid ${color}` }}>
                                    <div className="w-full h-full flex items-center justify-center text-[8px] font-bold" style={{ color }}>
                                      {p.toAp.slice(0, 2)}
                                    </div>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                          <div className="flex flex-wrap gap-3 mt-2">
                            {j.visitedAps.map((ap, i) => {
                              const colors = ['#0088ff', '#00c48f', '#ff8c00', '#a855f7', '#ff4757', '#ffb800', '#06b6d4', '#ec4899', '#84cc16'];
                              return (
                                <div key={ap} className="flex items-center gap-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                                  <span className="w-2.5 h-2.5 rounded-sm" style={{ background: colors[i % colors.length] }} />
                                  {ap}
                                </div>
                              );
                            })}
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Weak signal clients (legacy) */}
      {weakClients.length > 0 && (
        <div className="glass-card-solid overflow-x-auto">
          <h3 className="text-sm font-semibold p-4 pb-0" style={{ color: 'var(--text)' }}>
            Signal Issues ({weakClients.length})
          </h3>
          <table className="w-full text-sm">
            <thead>
              <tr>
                {['Client', 'Signal', 'Issues'].map((h) => (
                  <th key={h} className="text-left px-4 py-3 text-xs font-medium" style={{ color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {weakClients.map((c) => (
                <tr key={c.mac} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td className="px-4 py-3 font-medium" style={{ color: 'var(--text)' }}>{c.hostname || c.mac}</td>
                  <td className="px-4 py-3" style={{
                    color: c.signal >= -65 ? 'var(--success)' : c.signal >= -75 ? 'var(--warning)' : 'var(--error)',
                  }}>{c.signal} dBm</td>
                  <td className="px-4 py-3 flex flex-wrap gap-1">
                    {c.issues.map((issue) => (
                      <span key={issue} className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                        style={{ background: 'rgba(255,71,87,0.15)', color: 'var(--error)' }}>{issue}</span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {sorted.length === 0 && journeys.length === 0 && weakClients.length === 0 && (
        <div className="glass-card-solid p-8 text-center">
          <CheckCircle2 size={32} className="mx-auto mb-2" style={{ color: 'var(--success)' }} />
          <p style={{ color: 'var(--text-muted)' }}>No client issues detected</p>
        </div>
      )}
    </div>
  );
}

/* ── Channels Tab ──────────────────────────────── */

function ChannelsTab({
  aps,
  channelUsage,
}: {
  aps: ApAnalysis[];
  channelUsage: Record<string, string[]>;
}) {
  // Build from per-radio data with auto status
  type ChEntry = { band: string; channel: number; aps: { name: string; isAuto: boolean }[] };
  const map = new Map<string, ChEntry>();
  aps.forEach((ap) =>
    ap.radios.forEach((r) => {
      if (r.channel <= 0) return;
      const key = `${r.band}_ch${r.channel}`;
      const e = map.get(key);
      const apInfo = { name: ap.name, isAuto: !!r.isAuto };
      if (e) e.aps.push(apInfo);
      else map.set(key, { band: r.band, channel: r.channel, aps: [apInfo] });
    }),
  );
  const entries = [...map.values()];

  // Fallback to channelUsage if no radio data
  if (entries.length === 0 && Object.keys(channelUsage).length > 0) {
    for (const [key, apNames] of Object.entries(channelUsage)) {
      const parts = key.split('_ch');
      const band = parts[0] ?? '';
      const ch = parseInt(parts[1] ?? '0', 10);
      if (ch > 0) entries.push({ band, channel: ch, aps: apNames.map((n) => ({ name: n, isAuto: false })) });
    }
  }

  const bands = ['2.4GHz', '5GHz', '6GHz'];
  const grouped = bands.map((band) => ({
    band,
    channels: entries
      .filter((e) => e.band === band)
      .sort((a, b) => a.channel - b.channel),
  })).filter((g) => g.channels.length > 0);

  return (
    <div className="space-y-6">
      {grouped.map(({ band, channels }) => (
        <div key={band} className="glass-card-solid p-6">
          <h3
            className="text-sm font-semibold mb-4 flex items-center gap-2"
            style={{ color: 'var(--text)' }}
          >
            <span className="inline-block w-3 h-3 rounded-full"
              style={{ background: BAND_COLORS[band] ?? 'var(--primary)' }} />
            {band} Channels
          </h3>
          <div className="space-y-3">
            {channels.map(({ channel, aps: chAps }) => {
              const maxAps = Math.max(
                ...grouped.flatMap((g) => g.channels.map((c) => c.aps.length)),
              );
              const allAuto = chAps.every((a) => a.isAuto);
              const someAuto = chAps.some((a) => a.isAuto);
              const chLabel = `Ch ${channel}`;
              return (
                <div key={channel} className="flex items-center gap-3">
                  <span className="text-xs w-20 font-mono shrink-0 font-semibold"
                    style={{ color: 'var(--text)' }}>
                    {chLabel}
                    {allAuto && <span className="text-[9px] font-normal ml-1" style={{ color: 'var(--text-muted)' }}>(Auto)</span>}
                  </span>
                  <div
                    className="flex-1 h-5 rounded-full overflow-hidden relative"
                    style={{ background: 'var(--bg-elevated)' }}
                  >
                    <div className="h-full rounded-full" style={{
                      width: `${Math.max((chAps.length / maxAps) * 100, 8)}%`,
                      background: BAND_COLORS[band] ?? 'var(--primary)',
                      transition: 'width 0.6s ease',
                    }} />
                  </div>
                  <span className="text-xs font-semibold w-6 text-center shrink-0"
                    style={{ color: BAND_COLORS[band] ?? 'var(--primary)' }}>
                    {chAps.length}
                  </span>
                  <span className="text-xs shrink-0" style={{ color: 'var(--text-muted)' }}>
                    {chAps.map((a) => a.isAuto ? `${a.name}*` : a.name).join(', ')}
                    {someAuto && !allAuto && <span className="text-[9px] ml-1" style={{ color: 'var(--text-muted)' }}>(*=auto)</span>}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ── Recommendations Tab ───────────────────────── */

const ACTION_META: Record<string, { icon: string; label: string; color: string }> = {
  power_change: { icon: '⚡', label: 'TX Power', color: '#ff8c00' },
  channel_change: { icon: '📡', label: 'Channel', color: '#0088ff' },
  band_steering: { icon: '🔀', label: 'Band Steering', color: '#a855f7' },
  min_rssi: { icon: '📶', label: 'Min RSSI', color: '#00c48f' },
};

function RecsTab({
  findings,
  onPreview,
}: {
  findings: Finding[];
  onPreview: (ids: string[]) => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAll() {
    if (selected.size === findings.length) setSelected(new Set());
    else setSelected(new Set(findings.map((f) => f.id)));
  }

  // Group by category (action type)
  const grouped = new Map<string, Finding[]>();
  findings.forEach((f) => {
    const list = grouped.get(f.category) || [];
    list.push(f);
    grouped.set(f.category, list);
  });

  // Sort groups by the ACTION_META order, then alphabetically
  const groupOrder = Object.keys(ACTION_META);
  const sortedGroups = [...grouped.entries()].sort(([a], [b]) => {
    const ai = groupOrder.indexOf(a);
    const bi = groupOrder.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });

  // Toggle entire group
  function toggleGroup(cat: string) {
    const items = grouped.get(cat) ?? [];
    setSelected((prev) => {
      const next = new Set(prev);
      const allIn = items.every((f) => next.has(f.id));
      items.forEach((f) => (allIn ? next.delete(f.id) : next.add(f.id)));
      return next;
    });
  }

  return (
    <div className="space-y-4">
      {/* Sticky header bar — summary + action */}
      <div className="sticky top-0 z-20 glass-card-solid p-3 flex items-center justify-between gap-3"
        style={{ borderBottom: '2px solid var(--border)', backdropFilter: 'blur(12px)' }}>
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
            {findings.length} Recommendations
          </span>
          <div className="flex gap-1.5">
            {sortedGroups.map(([cat, items]) => {
              const meta = ACTION_META[cat];
              return (
                <span key={cat} className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                  style={{ background: `${meta?.color ?? 'var(--primary)'}15`, color: meta?.color ?? 'var(--text-muted)' }}>
                  {meta?.icon} {items.length}
                </span>
              );
            })}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={selectAll}
            className="text-xs font-medium px-3 py-1.5 rounded-lg cursor-pointer transition-colors"
            style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
            {selected.size === findings.length ? 'Deselect All' : 'Select All'}
          </button>
          {selected.size > 0 && (
            <button onClick={() => onPreview([...selected])}
              className="text-xs font-semibold px-4 py-1.5 rounded-lg cursor-pointer transition-all shadow-md flex items-center gap-1.5"
              style={{ background: 'var(--primary)', color: '#fff', border: 'none' }}>
              <Eye size={13} />
              Preview {selected.size} Change{selected.size !== 1 ? 's' : ''}
            </button>
          )}
        </div>
      </div>

      {/* Grouped recommendations */}
      {sortedGroups.map(([cat, items]) => {
        const meta = ACTION_META[cat] ?? { icon: '🔧', label: cat.replace(/_/g, ' '), color: 'var(--primary)' };
        const groupSelected = items.filter((f) => selected.has(f.id)).length;
        const allGroupSelected = groupSelected === items.length;
        return (
          <div key={cat} className="glass-card-solid overflow-hidden">
            {/* Group header — clickable to toggle group */}
            <div className="px-5 py-3 flex items-center gap-3 cursor-pointer select-none"
              onClick={() => toggleGroup(cat)}
              style={{ borderBottom: '1px solid var(--border)' }}>
              <input type="checkbox" checked={allGroupSelected}
                readOnly className="shrink-0 accent-[var(--primary)] pointer-events-none"
                {...(groupSelected > 0 && !allGroupSelected ? { ref: (el: HTMLInputElement | null) => { if (el) el.indeterminate = true; } } : {})}
              />
              <span className="text-base">{meta.icon}</span>
              <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{meta.label}</span>
              <span className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                style={{ background: `${meta.color}15`, color: meta.color }}>
                {items.length} {items.length === 1 ? 'change' : 'changes'}
              </span>
              {groupSelected > 0 && (
                <span className="ml-auto text-[10px] font-medium px-2 py-0.5 rounded-full"
                  style={{ background: 'rgba(0,136,255,0.1)', color: 'var(--primary)' }}>
                  {groupSelected} selected
                </span>
              )}
            </div>
            {/* Items */}
            <div>
              {items.map((f, i) => {
                const isSelected = selected.has(f.id);
                const deviceMatch = f.title.match(/^(.+?):\s*(.+)$/);
                const deviceName = deviceMatch ? deviceMatch[1] : '';
                const actionText = deviceMatch ? deviceMatch[2] : f.title;
                return (
                  <label key={f.id}
                    className="flex items-center gap-4 px-5 py-3 cursor-pointer transition-colors"
                    style={{
                      background: isSelected ? `${meta.color}08` : 'transparent',
                      borderBottom: i < items.length - 1 ? '1px solid var(--border)' : 'none',
                    }}>
                    <input type="checkbox" checked={isSelected} onChange={() => toggle(f.id)}
                      className="shrink-0 accent-[var(--primary)]" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        {deviceName && (
                          <span className="text-xs font-semibold" style={{ color: meta.color }}>{deviceName}</span>
                        )}
                        <PriorityBadge severity={f.severity} />
                      </div>
                      <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{f.description || actionText}</p>
                    </div>
                  </label>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── Main Page ─────────────────────────────────── */

const CACHE_KEY = 'unifi_last_analysis';

export default function AnalysisPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [job, setJob] =
    useState<AnalysisJob | null>(null);
  const [result, setResult] =
    useState<AnalysisResult | null>(null);
  const [error, setError] =
    useState<string | null>(null);
  const [tab, setTab] =
    useState<Tab>('overview');

  const poll = useCallback(async () => {
    if (!id || id === 'new') return;
    try {
      const j = await api.getAnalysisStatus(id);
      setJob(j);
      if (j.status === 'completed') {
        const r =
          await api.getAnalysisResults(id);
        setResult(r);
        // Cache the completed job for this session
        sessionStorage.setItem(CACHE_KEY, id);
      }
      if (j.status === 'failed') {
        setError(j.error ?? j.message ?? 'Analysis failed');
      }
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : 'Unknown error',
      );
    }
  }, [id]);

  useEffect(() => {
    if (id !== 'new') {
      const t = setTimeout(() => void poll(), 0);
      const iv = setInterval(() => {
        if (!result && !error) {
          void poll();
        }
      }, 2000);
      return () => { clearTimeout(t); clearInterval(iv); };
    }

    // Check for cached analysis from this session
    const cached = sessionStorage.getItem(CACHE_KEY);
    if (cached) {
      // Validate cached job still exists before redirect
      let cancelled = false;
      (async () => {
        try {
          const j = await api.getAnalysisStatus(cached);
          if (!cancelled) {
            if (j.status === 'completed') {
              navigate(`/analysis/${cached}`, { replace: true });
            } else {
              // Job no longer valid, clear and start fresh
              sessionStorage.removeItem(CACHE_KEY);
              const newJob = await api.runAnalysis();
              if (!cancelled) navigate(`/analysis/${newJob.jobId}`, { replace: true });
            }
          }
        } catch {
          // Job doesn't exist anymore, clear cache and start fresh
          if (!cancelled) {
            sessionStorage.removeItem(CACHE_KEY);
            try {
              const newJob = await api.runAnalysis();
              if (!cancelled) navigate(`/analysis/${newJob.jobId}`, { replace: true });
            } catch (e2) {
              if (!cancelled) setError(e2 instanceof Error ? e2.message : 'Failed to start analysis');
            }
          }
        }
      })();
      return () => { cancelled = true; };
    }

    // Auto-start analysis when id is "new"
    let cancelled = false;
    (async () => {
      try {
        const j = await api.runAnalysis();
        if (!cancelled) {
          navigate(`/analysis/${j.jobId}`, {
            replace: true,
          });
        }
      } catch (e) {
        if (!cancelled) {
          setError(
            e instanceof Error
              ? e.message
              : 'Failed to start analysis',
          );
        }
      }
    })();
    return () => { cancelled = true; };
  }, [id, poll, result, error, navigate]);

  function handleRerun() {
    sessionStorage.removeItem(CACHE_KEY);
    navigate('/analysis/new', { replace: true });
  }

  /* Loading skeleton */
  if (!result && !error) {
    return (
      <div
        className="max-w-6xl mx-auto space-y-4"
      >
        <div
          className="glass-card-solid p-8
            text-center"
        >
          <Loader2
            size={36}
            className="mx-auto mb-4
              animate-spin"
            style={{ color: 'var(--primary)' }}
          />
          <p
            className="text-lg font-semibold mb-1"
            style={{ color: 'var(--text)' }}
          >
            {job?.message || 'Starting analysis…'}
          </p>
          <p
            className="text-sm mb-4"
            style={{ color: 'var(--text-muted)' }}
          >
            {job
              ? `${job.progress}% complete`
              : 'Queuing…'}
          </p>
          {job && (
            <div
              className="max-w-md mx-auto h-3
                rounded-full overflow-hidden"
              style={{
                background: 'var(--bg-elevated)',
              }}
            >
              <div
                className="h-full rounded-full"
                style={{
                  width: `${job.progress}%`,
                  background: 'var(--primary)',
                  transition:
                    'width 0.5s ease',
                }}
              />
            </div>
          )}
        </div>
        <div
          className="grid grid-cols-1
            sm:grid-cols-2 lg:grid-cols-4 gap-4"
        >
          {[1, 2, 3, 4].map((i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="glass-card-solid p-8
          text-center"
      >
        <AlertTriangle
          size={32}
          className="mx-auto mb-2"
          style={{ color: 'var(--error)' }}
        />
        <p style={{ color: 'var(--text)' }}>
          {error}
        </p>
      </div>
    );
  }

  if (!result) return null;

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Tab Bar + Re-run */}
      <div className="flex items-center gap-3">
        <nav
          className="flex gap-1 p-1 rounded-xl
            flex-1"
          style={{
            background: 'var(--bg-elevated)',
          }}
        >
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className="flex-1 py-2 text-xs
                font-medium rounded-lg
                cursor-pointer transition-all"
              style={{
                background:
                  tab === t.key
                    ? 'var(--primary)'
                    : 'transparent',
                color:
                  tab === t.key
                    ? '#fff'
                    : 'var(--text-muted)',
                border: 'none',
              }}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <button
          onClick={handleRerun}
          className="flex items-center gap-1.5
            px-3 py-2 rounded-lg text-xs
            font-medium cursor-pointer
            transition-colors shrink-0"
          style={{
            background: 'var(--bg-elevated)',
            color: 'var(--text-muted)',
            border: '1px solid var(--border)',
          }}
          title="Re-run analysis"
        >
          <RefreshCw size={14} />
          Re-run
        </button>
      </div>

      {/* Tab Content */}
      {tab === 'overview' && (
        <OverviewTab result={result} />
      )}
      {tab === 'devices' && (
        <DevicesTab aps={result.aps} />
      )}
      {tab === 'clients' && (
        <ClientsTab
          clients={result.clients}
          journeys={result.clientJourneys}
          signalDistribution={result.signalDistribution}
          clientCapabilities={result.clientCapabilities}
          manufacturers={result.manufacturers}
          totalClients={result.clientCount}
        />
      )}
      {tab === 'channels' && (
        <ChannelsTab aps={result.aps} channelUsage={result.channelUsage} />
      )}
      {tab === 'recommendations' && (
        <RecsTab
          findings={result.findings}
          onPreview={(ids) => {
            navigate(
              `/repair?jobId=${encodeURIComponent(result.jobId)}&findings=${ids.join(',')}`,
            );
          }}
        />
      )}
    </div>
  );
}
