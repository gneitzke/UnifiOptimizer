import { Fragment } from 'react';
import type { TopologyModel, TopoNode } from '../model';
import { BAND_COLOR } from '../severity';
import { useMeasuredWidth } from './useMeasuredWidth';

/**
 * Layered network topology (docs/REPORT_SPEC.md §Topology): internet → gateway →
 * switches → APs → clients, one visual language (uniform rounded-rect nodes, one
 * type tag each — no mixed icons, no spaghetti). Wired links are neutral
 * hairlines; mesh backhaul links are coloured by the backend-given health band and
 * labelled with their RSSI, so a weak backhaul is visible at a glance. Positions
 * are layout only; every node, link, RSSI and health value is backend data.
 */

const NODE_W = 128;
const NODE_H = 46;
// Gap sized so the full internet→gateway→switch→AP→mesh→client stack (now six
// rows, with a dedicated mesh layer) still fits on one printed page under its
// section header, rather than the diagram spilling to a fresh page and stranding
// the header on an otherwise empty one.
const LAYER_GAP = 60;
const MAX_PER_LAYER = 8;
const MESH_STUB = 30; // health-coloured wireless-backhaul stub above a mesh node

interface Props {
  topology: TopologyModel;
  className?: string;
}

interface Placed {
  node: TopoNode;
  x: number; // center
  y: number; // center
}

export function TopologyDiagram({ topology, className }: Props) {
  const [ref, width] = useMeasuredWidth();
  const layers = topology.layers.filter((l) => l.nodes.length > 0);

  const height = Math.max(120, layers.length * NODE_H + (layers.length - 1) * LAYER_GAP + 24);
  const placed = new Map<string, Placed>();
  const overflow: { y: number; count: number }[] = [];

  layers.forEach((layer, li) => {
    const y = 12 + NODE_H / 2 + li * (NODE_H + LAYER_GAP);
    const shown = layer.nodes.slice(0, MAX_PER_LAYER);
    const hiddenCount = layer.nodes.length - shown.length;
    const cols = shown.length + (hiddenCount > 0 ? 1 : 0);
    const step = width / (cols + 1);
    shown.forEach((node, i) => {
      placed.set(node.id, { node, x: step * (i + 1), y });
    });
    if (hiddenCount > 0) overflow.push({ y, count: hiddenCount });
  });

  return (
    <div ref={ref} className={className}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Network topology, layered from internet to clients"
        style={{ display: 'block' }}
      >
        {/* Links first, behind the nodes. */}
        {topology.links.map((link, i) => {
          const a = placed.get(link.from);
          const b = placed.get(link.to);
          if (!a || !b) return null;
          const mesh = link.kind === 'mesh';
          const color = mesh ? BAND_COLOR[link.health ?? 'none'] : 'var(--hairline)';
          const mx = (a.x + b.x) / 2;
          const my = (a.y + b.y) / 2;
          const label =
            link.label ?? (link.rssi != null ? `${link.rssi} dBm` : null);
          return (
            <Fragment key={i}>
              <line
                x1={a.x}
                y1={a.y + NODE_H / 2}
                x2={b.x}
                y2={b.y - NODE_H / 2}
                stroke={color}
                strokeWidth={mesh ? 1.5 : 1}
                strokeDasharray={mesh ? '5 3' : undefined}
              />
              {mesh && label && (
                <text
                  x={mx + 6}
                  y={my}
                  dominantBaseline="middle"
                  className="tnum"
                  fontSize={10}
                  fill={color}
                >
                  {label}
                </text>
              )}
            </Fragment>
          );
        })}

        {/* Mesh backhaul shown on the node: the controller often does not report a
            mesh AP's parent, so the wireless uplink is a health-coloured dashed stub
            above the node with its RSSI, rather than an invented edge to a guess. */}
        {[...placed.values()].map(({ node, x, y }) => {
          if (!node.mesh_uplink) return null;
          const color = BAND_COLOR[node.mesh_uplink.health ?? 'none'];
          const top = y - NODE_H / 2;
          return (
            <g key={`mesh-${node.id}`}>
              <line
                x1={x}
                y1={top}
                x2={x}
                y2={top - MESH_STUB}
                stroke={color}
                strokeWidth={1.5}
                strokeDasharray="5 3"
              />
              <circle cx={x} cy={top - MESH_STUB} r={2.5} fill={color} />
              {node.mesh_uplink.rssi != null && (
                <text
                  x={x + 6}
                  y={top - MESH_STUB / 2}
                  dominantBaseline="middle"
                  className="tnum"
                  fontSize={10}
                  fill={color}
                >
                  {node.mesh_uplink.rssi} dBm
                </text>
              )}
            </g>
          );
        })}

        {/* Nodes. */}
        {[...placed.values()].map(({ node, x, y }) => (
          <Node key={node.id} node={node} x={x} y={y} />
        ))}

        {/* Honest overflow marker per layer. */}
        {overflow.map((o, i) => {
          const x = width - NODE_W / 2 - 8;
          return (
            <g key={`ov-${i}`}>
              <rect
                x={x - NODE_W / 2}
                y={o.y - NODE_H / 2}
                width={NODE_W}
                height={NODE_H}
                rx={8}
                fill="transparent"
                stroke="var(--hairline)"
                strokeDasharray="4 3"
              />
              <text
                x={x}
                y={o.y}
                textAnchor="middle"
                dominantBaseline="middle"
                className="tnum"
                fontSize={12}
                fill="var(--fg-subtle)"
              >
                +{o.count} more
              </text>
            </g>
          );
        })}
      </svg>

      {(topology.links.some((l) => l.kind === 'mesh') ||
        topology.layers.some((l) => l.nodes.some((n) => n.mesh_uplink))) && (
        <div className="flex flex-wrap items-center gap-4 mt-2 t-caption" style={{ color: 'var(--fg-muted)' }}>
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden className="inline-block w-5 border-t" style={{ borderColor: 'var(--hairline)' }} />
            Wired
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden className="inline-block w-5 border-t border-dashed" style={{ borderColor: BAND_COLOR.good }} />
            Wireless mesh backhaul (coloured by health, labelled with RSSI; parent AP not always reported)
          </span>
        </div>
      )}
    </div>
  );
}

const KIND_TAG: Record<TopoNode['kind'], string> = {
  internet: 'WAN',
  gateway: 'Gateway',
  switch: 'Switch',
  ap: 'Access point',
  mesh: 'Mesh AP',
  client: 'Clients',
};

function Node({ node, x, y }: { node: TopoNode; x: number; y: number }) {
  const left = x - NODE_W / 2;
  const top = y - NODE_H / 2;
  // A mesh node's border carries its backhaul health, so a weak uplink reads at a
  // glance alongside the health-coloured stub and RSSI above it.
  const meshHealth = node.mesh_uplink?.health ?? null;
  const stroke = meshHealth != null ? BAND_COLOR[meshHealth] : 'var(--strong)';
  return (
    <g>
      <rect
        x={left}
        y={top}
        width={NODE_W}
        height={NODE_H}
        rx={8}
        fill="var(--surface)"
        stroke={stroke}
        strokeWidth={meshHealth != null ? 1.5 : 1}
      />
      <text
        x={left + 10}
        y={top + 15}
        className="tnum"
        fontSize={9}
        fill="var(--fg-subtle)"
        style={{ letterSpacing: '0.03em', textTransform: 'uppercase' }}
      >
        {KIND_TAG[node.kind]}
      </text>
      <text
        x={left + 10}
        y={top + 30}
        fontSize={12}
        fontWeight={500}
        fill="var(--fg)"
      >
        {truncate(node.label, 16)}
      </text>
      {node.sublabel && (
        <text x={left + 10} y={top + 41} fontSize={9.5} fill="var(--fg-subtle)">
          {truncate(node.sublabel, 20)}
        </text>
      )}
      {node.badge && (
        <text
          x={left + NODE_W - 10}
          y={top + 30}
          textAnchor="end"
          className="tnum"
          fontSize={11}
          fill="var(--fg-muted)"
        >
          {node.badge}
        </text>
      )}
    </g>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
