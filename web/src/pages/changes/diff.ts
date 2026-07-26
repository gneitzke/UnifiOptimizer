/**
 * Structural before/after diff for the change ledger. The fix engine records a
 * `before` and `after` object per change; this flattens both to `path: value`
 * leaves and pairs them so the detail view can show exactly what a change
 * altered. Rendering (mono font, +/- markers — never red/green alone) lives in
 * DiffView; this module is pure data.
 */

export type DiffKind = 'added' | 'removed' | 'changed' | 'unchanged';

export interface DiffLine {
  path: string;
  kind: DiffKind;
  before: string | null;
  after: string | null;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Flatten nested objects to dotted leaf paths; arrays/scalars are leaves. */
function flatten(value: unknown, prefix = '', out: Record<string, string> = {}) {
  if (isObject(value)) {
    const keys = Object.keys(value);
    if (keys.length === 0) {
      out[prefix || '(empty)'] = '{}';
      return out;
    }
    for (const k of keys) {
      const path = prefix ? `${prefix}.${k}` : k;
      flatten(value[k], path, out);
    }
    return out;
  }
  out[prefix || '(root)'] = formatLeaf(value);
  return out;
}

function formatLeaf(v: unknown): string {
  if (v === null) return 'null';
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) return JSON.stringify(v);
  return String(v);
}

/**
 * Pair the flattened before/after into ordered diff lines. Order: changed and
 * added/removed first (what a reviewer cares about), then unchanged.
 */
export function diffObjects(
  before: Record<string, unknown>,
  after: Record<string, unknown>,
): DiffLine[] {
  const b = flatten(before);
  const a = flatten(after);
  const paths = Array.from(new Set([...Object.keys(b), ...Object.keys(a)])).sort();

  const lines: DiffLine[] = paths.map((path) => {
    const hasB = path in b;
    const hasA = path in a;
    const bv = hasB ? b[path] : null;
    const av = hasA ? a[path] : null;
    let kind: DiffKind;
    if (hasB && !hasA) kind = 'removed';
    else if (!hasB && hasA) kind = 'added';
    else if (bv !== av) kind = 'changed';
    else kind = 'unchanged';
    return { path, kind, before: bv, after: av };
  });

  const rank: Record<DiffKind, number> = { changed: 0, removed: 1, added: 2, unchanged: 3 };
  return lines.sort((x, y) => rank[x.kind] - rank[y.kind] || x.path.localeCompare(y.path));
}

export function diffSummary(lines: DiffLine[]): { changed: number; added: number; removed: number } {
  return lines.reduce(
    (acc, l) => {
      if (l.kind === 'changed') acc.changed += 1;
      else if (l.kind === 'added') acc.added += 1;
      else if (l.kind === 'removed') acc.removed += 1;
      return acc;
    },
    { changed: 0, added: 0, removed: 0 },
  );
}
