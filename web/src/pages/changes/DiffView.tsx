import { useMemo, useState } from 'react';
import { diffObjects, diffSummary, type DiffKind, type DiffLine } from './diff';

/**
 * Before/after diff in mono (DESIGN_FOUNDATION §Licensing: ui-monospace for
 * diffs). The change KIND is carried by a +/- marker and a path prefix — a
 * shape, not color alone (never-do rule 2) — with only a faint accent/neutral
 * tint. No red/green wall (rule 9). Unchanged leaves are collapsed behind a
 * toggle so the change reads at a glance.
 */

const MARK: Record<DiffKind, string> = {
  added: '+',
  removed: '−',
  changed: '~',
  unchanged: ' ',
};

function LineRow({ line }: { line: DiffLine }) {
  const { kind } = line;
  const accent =
    kind === 'added'
      ? 'var(--accent)'
      : kind === 'removed'
        ? 'var(--fg-subtle)'
        : kind === 'changed'
          ? 'var(--sev-p3)'
          : 'var(--fg-subtle)';
  const bg =
    kind === 'added'
      ? 'color-mix(in srgb, var(--accent) 7%, transparent)'
      : kind === 'changed'
        ? 'color-mix(in srgb, var(--sev-p3) 8%, transparent)'
        : kind === 'removed'
          ? 'color-mix(in srgb, var(--fg-subtle) 8%, transparent)'
          : 'transparent';

  return (
    <div
      className="grid font-mono"
      style={{
        gridTemplateColumns: '16px minmax(120px, 1fr) 1.4fr',
        gap: 8,
        fontSize: 12,
        lineHeight: '18px',
        background: bg,
        borderLeft: `2px solid ${kind === 'unchanged' ? 'transparent' : accent}`,
        padding: '1px 8px 1px 6px',
      }}
    >
      <span style={{ color: accent, userSelect: 'none' }}>{MARK[kind]}</span>
      <span style={{ color: 'var(--fg-muted)', overflowWrap: 'anywhere' }}>{line.path}</span>
      <span style={{ overflowWrap: 'anywhere' }}>
        {kind === 'changed' ? (
          <span>
            <span style={{ color: 'var(--fg-subtle)', textDecoration: 'line-through' }}>
              {line.before ?? '—'}
            </span>
            <span style={{ color: 'var(--fg-subtle)' }}>{'  →  '}</span>
            <span style={{ color: 'var(--fg)' }}>{line.after ?? '—'}</span>
          </span>
        ) : kind === 'removed' ? (
          <span style={{ color: 'var(--fg-subtle)', textDecoration: 'line-through' }}>
            {line.before ?? '—'}
          </span>
        ) : (
          <span style={{ color: kind === 'added' ? 'var(--fg)' : 'var(--fg-muted)' }}>
            {line.after ?? '—'}
          </span>
        )}
      </span>
    </div>
  );
}

export function DiffView({
  before,
  after,
}: {
  before: Record<string, unknown>;
  after: Record<string, unknown>;
}) {
  const lines = useMemo(() => diffObjects(before, after), [before, after]);
  const summary = useMemo(() => diffSummary(lines), [lines]);
  const [showUnchanged, setShowUnchanged] = useState(false);

  const visible = showUnchanged ? lines : lines.filter((l) => l.kind !== 'unchanged');
  const unchangedCount = lines.length - lines.filter((l) => l.kind !== 'unchanged').length;

  if (lines.length === 0) {
    return (
      <div className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
        No recorded before/after state for this change.
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-2">
        <span className="t-caption tnum" style={{ color: 'var(--fg-muted)' }}>
          {summary.changed} changed · {summary.added} added · {summary.removed} removed
        </span>
        {unchangedCount > 0 && (
          <button
            type="button"
            className="t-caption ml-auto cursor-pointer"
            style={{ color: 'var(--accent)' }}
            onClick={() => setShowUnchanged((s) => !s)}
          >
            {showUnchanged ? 'Hide' : 'Show'} {unchangedCount} unchanged
          </button>
        )}
      </div>
      <div
        className="rounded-control overflow-hidden overflow-x-auto"
        style={{ border: '1px solid var(--hairline)' }}
      >
        {visible.length === 0 ? (
          <div className="font-mono p-2" style={{ fontSize: 12, color: 'var(--fg-subtle)' }}>
            No field-level differences.
          </div>
        ) : (
          visible.map((l) => <LineRow key={l.path} line={l} />)
        )}
      </div>
    </div>
  );
}
