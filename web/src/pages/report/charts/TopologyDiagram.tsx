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
 *
 * A layer's row is sized from the MEASURED width, never assumed: box width comes
 * out of the available width, the node count and a minimum gap, so boxes can
 * never overlap at any count. Once boxes would fall below a readable floor, the
 * layer wraps onto additional (vertically stacked) rows instead of shrinking
 * further, and the SVG grows to fit — a 9-AP row reads as two balanced rows of
 * legible boxes, never five squeezed-together slivers.
 */

const NODE_W = 128; // preferred (max) box width — unchanged from the original design
const NODE_H = 46;
const MIN_NODE_W = 84; // floor before a row wraps rather than shrinking further
const GAP_MIN = 14; // minimum horizontal gap between boxes, and their side margin
// Gap sized so the full internet→gateway→switch→AP→mesh→client stack (six
// layers, with a dedicated mesh layer, PLUS a wrapped second AP row on a
// crowded site) still fits on one printed page under its section header
// (Section's `keepHeadingWithContent`), rather than the diagram spilling to a
// fresh page and stranding the header on an otherwise empty one (Gitea #27). A
// fleet large enough to need a THIRD wrapped row will still outgrow one page —
// an honest overflow onto its own page beats a cramped, illegible diagram.
const LAYER_GAP = 40;
const ROW_GAP = 12; // vertical gap between a layer's own wrapped rows
// Real sites run well past 8 APs; wrapping (not truncation) is now how a crowded
// layer stays legible, so this is a safety valve for pathological input, not the
// everyday ceiling it used to be.
const MAX_PER_LAYER = 24;
const MESH_STUB = 30; // health-coloured wireless-backhaul stub above a mesh node

interface Props {
  topology: TopologyModel;
  className?: string;
}

interface Placed {
  node: TopoNode;
  x: number; // center
  y: number; // center
  w: number; // this node's box width — varies with how crowded its row is
}

interface OverflowMarker {
  x: number;
  y: number;
  w: number;
  count: number;
}

type RowItem = { kind: 'node'; node: TopoNode } | { kind: 'overflow'; count: number };

/** How many MIN_NODE_W-wide boxes (with GAP_MIN gaps and margins) fit across `width`. */
function maxPerRow(width: number): number {
  return Math.max(1, Math.floor((width - GAP_MIN) / (MIN_NODE_W + GAP_MIN)));
}

/** Split `n` items across `rows` as evenly as possible (bigger rows first), so a
 *  wrapped layer never leaves a lonely single box on its last row. */
function balancedRowSizes(n: number, rows: number): number[] {
  const base = Math.floor(n / rows);
  const remainder = n % rows;
  return Array.from({ length: rows }, (_, i) => base + (i < remainder ? 1 : 0));
}

export function TopologyDiagram({ topology, className }: Props) {
  const [ref, width] = useMeasuredWidth();
  const layers = topology.layers.filter((l) => l.nodes.length > 0);

  const placed = new Map<string, Placed>();
  const overflow: OverflowMarker[] = [];

  let cursorY = 12 + NODE_H / 2;
  layers.forEach((layer, li) => {
    const shown = layer.nodes.slice(0, MAX_PER_LAYER);
    const hiddenCount = layer.nodes.length - shown.length;
    const items: RowItem[] = shown.map((node) => ({ kind: 'node', node }) as RowItem);
    if (hiddenCount > 0) items.push({ kind: 'overflow', count: hiddenCount });

    const perRowMax = maxPerRow(width);
    const rowCount = Math.max(1, Math.ceil(items.length / perRowMax));
    const rowSizes = balancedRowSizes(items.length, rowCount);
    const widestRow = Math.max(...rowSizes);
    const boxW = Math.min(
      NODE_W,
      Math.max(MIN_NODE_W, (width - (widestRow + 1) * GAP_MIN) / widestRow),
    );

    if (li > 0) cursorY += NODE_H + LAYER_GAP;
    let itemIndex = 0;
    rowSizes.forEach((size, ri) => {
      if (ri > 0) cursorY += NODE_H + ROW_GAP;
      const rowWidth = size * boxW + (size - 1) * GAP_MIN;
      const left = (width - rowWidth) / 2;
      for (let i = 0; i < size; i++) {
        const item = items[itemIndex++];
        const x = left + boxW / 2 + i * (boxW + GAP_MIN);
        if (item.kind === 'node') {
          placed.set(item.node.id, { node: item.node, x, y: cursorY, w: boxW });
        } else {
          overflow.push({ x, y: cursorY, w: boxW, count: item.count });
        }
      }
    });
  });
  const height = Math.max(120, cursorY + NODE_H / 2 + 24);
  const hasMesh =
    topology.links.some((l) => l.kind === 'mesh') ||
    topology.layers.some((l) => l.nodes.some((n) => n.mesh_uplink));
  const hasBadges = topology.layers.some((l) => l.nodes.some((n) => n.badge));

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
          // The legend promises every mesh link is "coloured by health, labelled
          // with RSSI" (Gitea #27) — an honest "RSSI n/a" keeps that true even
          // when the controller didn't report a reading, instead of silently
          // dropping the label and breaking the promise for that one edge.
          const label =
            link.label ?? (link.rssi != null ? `${link.rssi} dBm` : 'RSSI n/a');
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
              {mesh && (
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
              <text
                x={x + 6}
                y={top - MESH_STUB / 2}
                dominantBaseline="middle"
                className="tnum"
                fontSize={10}
                fill={color}
              >
                {node.mesh_uplink.rssi != null ? `${node.mesh_uplink.rssi} dBm` : 'RSSI n/a'}
              </text>
            </g>
          );
        })}

        {/* Nodes. */}
        {[...placed.values()].map(({ node, x, y, w }) => (
          <Node key={node.id} node={node} x={x} y={y} w={w} />
        ))}

        {/* Honest overflow marker, one per layer that still exceeds MAX_PER_LAYER. */}
        {overflow.map((o, i) => (
          <g key={`ov-${i}`}>
            <rect
              x={o.x - o.w / 2}
              y={o.y - NODE_H / 2}
              width={o.w}
              height={NODE_H}
              rx={8}
              fill="transparent"
              stroke="var(--hairline)"
              strokeDasharray="4 3"
            />
            <text
              x={o.x}
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
        ))}
      </svg>

      {(hasMesh || hasBadges) && (
        <div className="flex flex-wrap items-center gap-4 mt-2 t-caption" style={{ color: 'var(--fg-muted)' }}>
          {hasMesh && (
            <>
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden className="inline-block w-5 border-t" style={{ borderColor: 'var(--hairline)' }} />
                Wired
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden className="inline-block w-5 border-t border-dashed" style={{ borderColor: BAND_COLOR.good }} />
                Wireless mesh backhaul (coloured by health, labelled with RSSI; parent AP not always reported)
              </span>
            </>
          )}
          {/* The per-AP number is otherwise a bare digit with nothing saying what
              it counts (Gitea #27) — one legend line covers every node at once
              rather than repeating a unit on each small badge. */}
          {hasBadges && <span className="inline-flex items-center gap-1.5">Number = connected clients</span>}
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

/** How many characters fit inside a box of width `w` at `fontSize`, so truncation
 *  always fits the box it's drawn in rather than a fixed guess. `reserveRight`
 *  carves out room for a badge sharing the same text line. */
function maxCharsForWidth(w: number, fontSize: number, reserveRight = 0): number {
  const avgAdvance = fontSize * 0.58; // Inter's roughly average glyph advance at this size
  const usable = w - 20 - reserveRight; // 10px inner padding on each side
  return Math.max(3, Math.floor(usable / avgAdvance));
}

function Node({ node, x, y, w }: { node: TopoNode; x: number; y: number; w: number }) {
  const left = x - w / 2;
  const top = y - NODE_H / 2;
  // A mesh node's border carries its backhaul health, so a weak uplink reads at a
  // glance alongside the health-coloured stub and RSSI above it.
  const meshHealth = node.mesh_uplink?.health ?? null;
  const stroke = meshHealth != null ? BAND_COLOR[meshHealth] : 'var(--strong)';
  // Room reserved on the label's line for the right-aligned client-count badge, so
  // a long name is truncated before it ever reaches the badge, not clipped by it.
  const badgeReserve = node.badge ? node.badge.length * 7 + 8 : 0;
  return (
    <g>
      <rect
        x={left}
        y={top}
        width={w}
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
        <title>{KIND_TAG[node.kind]}</title>
        {truncate(KIND_TAG[node.kind], maxCharsForWidth(w, 9))}
      </text>
      <text
        x={left + 10}
        y={top + 30}
        fontSize={12}
        fontWeight={500}
        fill="var(--fg)"
      >
        {/* The box is fixed-width, so a long device name is shortened to fit. The
            full name stays here: a truncated label is the only thing identifying
            the node, and two devices sharing a prefix would otherwise be
            indistinguishable with no way to recover either. */}
        <title>{node.label}</title>
        {truncate(node.label, maxCharsForWidth(w, 12, badgeReserve))}
      </text>
      {node.sublabel && (
        <text x={left + 10} y={top + 41} fontSize={9.5} fill="var(--fg-subtle)">
          <title>{node.sublabel}</title>
          {truncate(node.sublabel, maxCharsForWidth(w, 9.5))}
        </text>
      )}
      {node.badge && (
        <text
          x={left + w - 10}
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

function truncate(s: string, maxChars: number): string {
  return s.length > maxChars ? `${s.slice(0, Math.max(1, maxChars - 1))}…` : s;
}
