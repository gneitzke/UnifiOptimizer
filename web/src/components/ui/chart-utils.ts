/**
 * Hand-rolled SVG chart helpers (docs §Charts). No chart library — these back
 * Sparkline / TimeSeriesChart / RangeBar. The rules they enforce:
 * gaps stay gaps (never interpolated), bar baselines at zero, axes never
 * truncated, percentage axes fixed 0-100, ~4 horizontal gridlines.
 */

export interface ChartPoint {
  ts: number; // epoch seconds
  value: number | null; // null == data gap (rendered as a gap)
  /** Optional intra-bucket envelope from a server rollup ({min,max,avg}). When
   *  present and wider than the avg, a faint spread band is drawn behind the line
   *  so an averaged-away trough/spike is not invisible. */
  min?: number | null;
  max?: number | null;
}

export interface Scale {
  (v: number): number;
}

/** Linear map from data domain to pixel range. */
export function linScale(
  d0: number,
  d1: number,
  r0: number,
  r1: number,
): Scale {
  const span = d1 - d0 || 1;
  return (v: number) => r0 + ((v - d0) / span) * (r1 - r0);
}

/** "Nice" rounded step for an axis (1/2/5 × 10^n). */
function niceStep(rough: number): number {
  const mag = Math.pow(10, Math.floor(Math.log10(rough || 1)));
  const norm = rough / mag;
  const step = norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1;
  return step * mag;
}

export interface NiceScale {
  min: number;
  max: number;
  ticks: number[];
}

/**
 * A non-truncated y-scale with ~`count` gridlines (never-do rule 9).
 *
 * `percentage` locks the axis to 0-100 like Apple's battery chart. A `domain`
 * (meaning-anchored fixed min/max, e.g. RSSI −90..−30 dBm) pins the axis so the
 * same value reads the same across windows and never floats to hug the data max.
 * By default the axis includes zero (`zeroBaseline`) so counts/rates/bars sit on
 * a true zero baseline. For a *level* metric with no fixed domain — noise, SFP
 * power (dBm) — pass `zeroBaseline: false` so the axis frames the data's own
 * padded min/max and the trend stays readable (the zero-baseline rule in the
 * design doc is scoped to bars, not to level lines).
 */
export function niceYScale(
  values: number[],
  opts: {
    percentage?: boolean;
    count?: number;
    zeroBaseline?: boolean;
    domain?: { min: number; max: number };
  } = {},
): NiceScale {
  const count = opts.count ?? 4;
  const zeroBaseline = opts.zeroBaseline ?? true;
  if (opts.percentage) {
    return { min: 0, max: 100, ticks: [0, 25, 50, 75, 100] };
  }
  if (opts.domain) {
    // A fixed, meaning-anchored axis. Ticks step up from the true min so the
    // domain endpoints are honoured (never a floating frame that hugs the data).
    const { min, max } = opts.domain;
    const step = niceStep((max - min) / count) || 1;
    const ticks: number[] = [];
    for (let t = min; t <= max + step / 2; t += step) {
      ticks.push(Number(t.toFixed(6)));
    }
    return { min, max, ticks };
  }
  const finite = values.filter((v) => Number.isFinite(v));
  if (!finite.length) {
    return { min: 0, max: 1, ticks: [0, 1] };
  }
  let rawMin = Math.min(...finite);
  let rawMax = Math.max(...finite);
  if (zeroBaseline) {
    rawMax = Math.max(rawMax, 0);
    rawMin = Math.min(rawMin, 0); // baselines at zero
  } else if (rawMax !== rawMin) {
    // Frame to the data window with ~15% headroom on each side so a flat-looking
    // level (e.g. -77..-73 dBm) fills the plot instead of hugging an edge.
    const pad = (rawMax - rawMin) * 0.15;
    rawMin -= pad;
    rawMax += pad;
  }
  if (rawMax === rawMin) {
    if (zeroBaseline) {
      const m = rawMax === 0 ? 1 : rawMax * 1.2;
      return { min: 0, max: m, ticks: [0, m] };
    }
    // A single distinct level: center it in a small nice window.
    const pad = Math.max(1, Math.abs(rawMax) * 0.05);
    rawMin -= pad;
    rawMax += pad;
  }
  const step = niceStep((rawMax - rawMin) / count);
  const min = Math.floor(rawMin / step) * step;
  const max = Math.ceil(rawMax / step) * step;
  const ticks: number[] = [];
  for (let t = min; t <= max + step / 2; t += step) ticks.push(Number(t.toFixed(6)));
  return { min, max, ticks };
}

/**
 * Build an SVG path for a line, breaking it wherever the value is null so gaps
 * read as gaps (never-do rule 8). Returns a "M…L…" string with sub-paths.
 */
export function linePath(
  points: ChartPoint[],
  x: Scale,
  y: Scale,
): string {
  let d = '';
  let pen = false;
  for (const p of points) {
    if (p.value == null || !Number.isFinite(p.value)) {
      pen = false;
      continue;
    }
    const px = x(p.ts);
    const py = y(p.value);
    d += `${pen ? 'L' : 'M'}${px.toFixed(2)},${py.toFixed(2)} `;
    pen = true;
  }
  return d.trim();
}

/**
 * Area sub-paths, one PER contiguous finite run, each closed straight down to
 * `baseY` at its own first/last x. A single string closed to the global corners
 * would run a diagonal across a null gap and paint a fill wedge under data that
 * does not exist (never-do rule 8 / rule 11); building per run keeps every fill
 * under real, continuous data only. A one-point run has zero width (invisible).
 */
export function areaPaths(
  points: ChartPoint[],
  x: Scale,
  y: Scale,
  baseY: number,
): string[] {
  const out: string[] = [];
  let run: ChartPoint[] = [];
  const flush = () => {
    if (run.length > 0) {
      const x0 = x(run[0].ts);
      const xN = x(run[run.length - 1].ts);
      let d = `M${x0.toFixed(2)},${baseY.toFixed(2)} `;
      for (const p of run) d += `L${x(p.ts).toFixed(2)},${y(p.value as number).toFixed(2)} `;
      d += `L${xN.toFixed(2)},${baseY.toFixed(2)} Z`;
      out.push(d);
    }
    run = [];
  };
  for (const p of points) {
    if (p.value == null || !Number.isFinite(p.value)) flush();
    else run.push(p);
  }
  flush();
  return out;
}

/**
 * Min/max envelope polygons (server rollup spread), one per contiguous run,
 * drawn faintly behind the avg line so an intra-bucket trough/spike averaged into
 * `avg` is still visible. A run is emitted only when some bucket actually has
 * `max > min` (raw n=1 data collapses to the line and yields nothing).
 */
export function bandPaths(points: ChartPoint[], x: Scale, y: Scale): string[] {
  const out: string[] = [];
  let run: ChartPoint[] = [];
  const hasEnv = (p: ChartPoint): boolean =>
    p.value != null &&
    Number.isFinite(p.value) &&
    p.min != null &&
    Number.isFinite(p.min) &&
    p.max != null &&
    Number.isFinite(p.max);
  const flush = () => {
    if (run.length > 0 && run.some((p) => (p.max as number) > (p.min as number))) {
      let d = '';
      run.forEach((p, i) => {
        d += `${i === 0 ? 'M' : 'L'}${x(p.ts).toFixed(2)},${y(p.max as number).toFixed(2)} `;
      });
      for (let i = run.length - 1; i >= 0; i--) {
        d += `L${x(run[i].ts).toFixed(2)},${y(run[i].min as number).toFixed(2)} `;
      }
      d += 'Z';
      out.push(d);
    }
    run = [];
  };
  for (const p of points) {
    if (!hasEnv(p)) flush();
    else run.push(p);
  }
  flush();
  return out;
}

/** Compact numeric formatting for axis labels and annotations. */
export function fmt(n: number | null | undefined, digits = 0): string {
  if (n == null || !Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}k`;
  return n.toFixed(digits);
}

/** Local HH:MM for a scrub annotation (store is UTC; display local). */
export function clockLabel(ts: number): string {
  const d = new Date(ts * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

/** Local "Jul 17" for a multi-day axis — so a 7-day window never reads as one day
 *  (store is UTC; display local). */
export function dayLabel(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/** Local "Jul 17, 14:30" for a multi-day scrub annotation (store UTC; display local). */
export function dayTimeLabel(ts: number): string {
  return `${dayLabel(ts)}, ${clockLabel(ts)}`;
}

/** Local HH:MM:SS for an "as of" staleness stamp (store is UTC; display local). */
export function clockLabelSec(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** Nearest point index to a pixel x, for scrub selection. */
export function nearestIndex(points: ChartPoint[], x: Scale, px: number): number {
  let best = 0;
  let bestD = Infinity;
  for (let i = 0; i < points.length; i++) {
    const d = Math.abs(x(points[i].ts) - px);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return best;
}
