/**
 * The report's honest empty state (docs/REPORT_SPEC.md §Honesty conventions).
 * When a query returned nothing for a section, the report says so plainly rather
 * than inventing a chart or a zero — an absent measurement is not a good score.
 */

interface Props {
  /** What was unavailable, in plain words. */
  label: string;
  /** Optional reason (e.g. "no mesh APs on this site"). */
  reason?: string;
}

export function NoData({ label, reason }: Props) {
  return (
    <div
      className="report-keep flex flex-col gap-0.5 rounded-card px-4 py-3"
      style={{ border: '1px dashed var(--hairline)', background: 'transparent' }}
    >
      <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
        {label}
      </span>
      {reason && (
        <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
          {reason}
        </span>
      )}
    </div>
  );
}
