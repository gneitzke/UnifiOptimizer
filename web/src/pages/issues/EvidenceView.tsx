import { Fragment, type ReactNode } from 'react';
import { humanizeKey } from '../shared/format';

/**
 * Renders an issue's `evidence` blob as compact labeled numbers + small tables —
 * the numbers that justify the finding, verbatim — never a raw JSON dump. Keys
 * consumed elsewhere (confounders, chart hints) are omitted so this stays the
 * "supporting measurements" view. Scalars right-align tabular; nested objects
 * become sub-groups; arrays of objects become mini tables.
 */

const OMIT_KEYS = new Set(['confounders_checked', 'series_hints', 'signals']);

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function numberStr(n: number): string {
  if (Number.isInteger(n)) return String(n);
  const r = Math.round(n * 1000) / 1000;
  return String(r);
}

function scalarText(v: unknown): string {
  if (v == null) return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'number') return Number.isFinite(v) ? numberStr(v) : '—';
  return String(v);
}

function isScalar(v: unknown): boolean {
  return v == null || ['number', 'string', 'boolean'].includes(typeof v);
}

function ScalarRow({ label, value }: { label: string; value: unknown }) {
  const numeric = typeof value === 'number';
  return (
    <div
      className="flex items-baseline justify-between gap-4 py-1.5"
      style={{ borderTop: '1px solid var(--hairline)' }}
    >
      <span className="t-caption" style={{ color: 'var(--fg-muted)' }}>
        {label}
      </span>
      <span
        className={numeric ? 't-body tnum text-right' : 't-body text-right'}
        style={{ color: 'var(--fg)', wordBreak: 'break-word' }}
      >
        {scalarText(value)}
      </span>
    </div>
  );
}

function ScalarArrayRow({ label, value }: { label: string; value: unknown[] }) {
  return (
    <div
      className="flex items-baseline justify-between gap-4 py-1.5"
      style={{ borderTop: '1px solid var(--hairline)' }}
    >
      <span className="t-caption" style={{ color: 'var(--fg-muted)' }}>
        {label}
      </span>
      <span className="t-body text-right" style={{ color: 'var(--fg)' }}>
        {value.map(scalarText).join(', ') || '—'}
      </span>
    </div>
  );
}

function ObjectArrayTable({ label, rows }: { label: string; rows: Record<string, unknown>[] }) {
  const cols = Array.from(
    rows.reduce((set, r) => {
      Object.keys(r).forEach((k) => set.add(k));
      return set;
    }, new Set<string>()),
  );
  return (
    <div className="py-1.5" style={{ borderTop: '1px solid var(--hairline)' }}>
      <div className="t-caption mb-1" style={{ color: 'var(--fg-muted)' }}>
        {label}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              {cols.map((c) => (
                <th
                  key={c}
                  className="text-left px-2 py-1 t-micro font-medium"
                  style={{ color: 'var(--fg-subtle)', borderBottom: '1px solid var(--hairline)' }}
                >
                  {humanizeKey(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {cols.map((c) => {
                  const v = r[c];
                  return (
                    <td
                      key={c}
                      className={typeof v === 'number' ? 'px-2 py-1 tnum' : 'px-2 py-1'}
                      style={{ color: 'var(--fg)' }}
                    >
                      {isScalar(v) ? scalarText(v) : JSON.stringify(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function renderEntry(key: string, value: unknown, depth: number): ReactNode {
  const label = humanizeKey(key);
  if (isScalar(value)) return <ScalarRow key={key} label={label} value={value} />;
  if (Array.isArray(value)) {
    if (value.length === 0) return <ScalarRow key={key} label={label} value="—" />;
    if (value.every(isScalar)) return <ScalarArrayRow key={key} label={label} value={value} />;
    if (value.every(isRecord)) {
      return <ObjectArrayTable key={key} label={label} rows={value as Record<string, unknown>[]} />;
    }
    return <ScalarArrayRow key={key} label={label} value={value.map((v) => JSON.stringify(v))} />;
  }
  if (isRecord(value) && depth < 2) {
    const entries = Object.entries(value).filter(([k]) => !OMIT_KEYS.has(k));
    return (
      <div key={key} className="py-1.5" style={{ borderTop: '1px solid var(--hairline)' }}>
        <div className="t-caption mb-0.5" style={{ color: 'var(--fg-muted)' }}>
          {label}
        </div>
        <div className="pl-3">
          {entries.map(([k, v]) => (
            <Fragment key={k}>{renderEntry(k, v, depth + 1)}</Fragment>
          ))}
        </div>
      </div>
    );
  }
  return <ScalarRow key={key} label={label} value={JSON.stringify(value)} />;
}

export function EvidenceView({ evidence }: { evidence: Record<string, unknown> }) {
  const entries = Object.entries(evidence).filter(([k]) => !OMIT_KEYS.has(k));
  if (entries.length === 0) {
    return (
      <p className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
        No supporting measurements were attached to this issue.
      </p>
    );
  }
  return (
    <div className="flex flex-col">
      {entries.map(([k, v]) => renderEntry(k, v, 0))}
    </div>
  );
}
