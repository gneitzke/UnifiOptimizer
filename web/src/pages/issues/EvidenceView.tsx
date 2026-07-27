import { Fragment, type ReactNode } from 'react';
import { formatWithUnit, humanizeKeyForUnit, inferUnit, scalarText } from '../shared/format';
import type { EvidenceFieldLayout } from '../shared/api';

/**
 * Renders an issue's `evidence` blob as compact labeled numbers + small tables —
 * the numbers that justify the finding, verbatim — never a raw JSON dump. Keys
 * consumed elsewhere (confounders, chart hints) are omitted so this stays the
 * "supporting measurements" view. Scalars right-align tabular; nested objects
 * become sub-groups; arrays of objects become mini tables.
 *
 * `layout` (from the API's `evidence_layout`, sourced from the detector's
 * catalog Playbook — Gitea #18) supplies a proper label, unit, and narrative
 * order for the keys a detector has documented; those render first, in that
 * order. Everything else still renders — in the evidence dict's own insertion
 * order, which is the detector's narrative order once it survives storage
 * un-sorted — generically humanized, with a conservative unit inferred from
 * the key's suffix (`_ms`, `_dbm`, `_fraction`, …) when one applies. A
 * detector with no layout at all still renders exactly as before.
 */

const OMIT_KEYS = new Set(['confounders_checked', 'series_hints', 'signals']);

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function isScalar(v: unknown): boolean {
  return v == null || ['number', 'string', 'boolean'].includes(typeof v);
}

/** A scalar's display text: an explicit unit wins, else infer one from `key`. */
function scalarTextFor(key: string, value: unknown): string {
  const inferred = inferUnit(key);
  if (inferred && typeof value === 'number') {
    return formatWithUnit(value, inferred.unit, inferred.percent, inferred.duration);
  }
  return scalarText(value);
}

function ScalarRow({
  label,
  value,
  display,
}: {
  label: string;
  value: unknown;
  /** Pre-formatted display text; falls back to `scalarText(value)`. */
  display?: string;
}) {
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
        {display ?? scalarText(value)}
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

/** A table column header: the humanized key, plus an inferred unit in parens
 * ("RSSI (dBm)") so a mini-table's numbers carry the same honesty as a row. */
function columnLabel(key: string): string {
  const inferred = inferUnit(key);
  const base = humanizeKeyForUnit(key, inferred);
  return inferred?.unit ? `${base} (${inferred.unit})` : base;
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
                  {columnLabel(c)}
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

/** One evidence key -> its row/table/sub-group, recursing into nested objects.
 * `field` (from `evidence_layout`, when this key was reached via the laid-out
 * pass) supplies the label and — for a scalar value — the unit; absent, the
 * label falls back to `humanizeKeyForUnit` (the key humanized minus a unit-
 * bearing suffix, so a "_dbm" key never repeats itself next to its own unit).
 * Only the scalar branch consumes the unit: a laid-out key whose value turns
 * out to be an object/array (a detector's evidence shape drifted) still routes
 * through the matching table/sub-group renderer, never mis-renders as text. */
function renderEntry(
  key: string,
  value: unknown,
  depth: number,
  field?: EvidenceFieldLayout,
): ReactNode {
  const label = field?.label ?? humanizeKeyForUnit(key, inferUnit(key));
  if (isScalar(value)) {
    const display = field
      ? formatWithUnit(value, field.unit, field.percent, field.duration)
      : scalarTextFor(key, value);
    return <ScalarRow key={key} label={label} value={value} display={display} />;
  }
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

export function EvidenceView({
  evidence,
  layout = [],
}: {
  evidence: Record<string, unknown>;
  /** Narrative label/unit/order for this detector's evidence, from the API's
   * `evidence_layout` (netadmin.detect.catalog.Playbook.evidence_fields). */
  layout?: EvidenceFieldLayout[];
}) {
  const allEntries = Object.entries(evidence).filter(([k]) => !OMIT_KEYS.has(k));
  if (allEntries.length === 0) {
    return (
      <p className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
        No supporting measurements were attached to this issue.
      </p>
    );
  }
  const evidenceMap = new Map(allEntries);
  const fieldByKey = new Map(layout.map((f) => [f.key, f]));
  // The playbook's order first (its narrative), then whatever it didn't name —
  // in the evidence dict's own order, which is the detector's own narrative
  // order once storage stops alphabetising it.
  const laidOutKeys = layout.filter((f) => evidenceMap.has(f.key)).map((f) => f.key);
  const usedKeys = new Set(laidOutKeys);
  const rest = allEntries.filter(([k]) => !usedKeys.has(k));

  return (
    <div className="flex flex-col">
      {laidOutKeys.map((k) => (
        <Fragment key={k}>{renderEntry(k, evidenceMap.get(k), 0, fieldByKey.get(k))}</Fragment>
      ))}
      {rest.map(([k, v]) => renderEntry(k, v, 0))}
    </div>
  );
}
