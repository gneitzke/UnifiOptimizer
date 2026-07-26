import type { Band, SeverityCounts } from '../model';
import { BAND_COLOR, BAND_WORD, SEVERITY_COLORS, SEVERITY_LABEL, SEVERITY_ORDER } from '../severity';
import { SeverityGlyph } from './SeverityChip';

/**
 * The executive scorecard (docs/REPORT_SPEC.md §Executive summary): the overall
 * health score with its band, and the findings-by-severity counts in the CVSS
 * colour system. Both come straight from the model — the count row is rendered,
 * not tallied here.
 */
export function Scorecard({
  score,
  band,
  counts,
  lowConfidence = false,
  confidenceNote = null,
}: {
  score: number | null;
  band: Band;
  counts: SeverityCounts;
  lowConfidence?: boolean;
  confidenceNote?: string | null;
}) {
  return (
    <div className="report-keep flex flex-col gap-2">
    <div
      className="grid gap-0 rounded-card overflow-hidden"
      style={{ border: '1px solid var(--hairline)', gridTemplateColumns: 'minmax(180px, 240px) 1fr' }}
    >
      {/* Overall score */}
      <div
        className="flex flex-col justify-center gap-1 p-5"
        style={{ borderRight: '1px solid var(--hairline)', background: 'var(--canvas)' }}
      >
        <span className="t-label" style={{ color: 'var(--fg-muted)' }}>
          Overall health
        </span>
        <div className="flex items-baseline gap-2">
          <span className="t-metric" style={{ color: 'var(--fg)', fontSize: 40, lineHeight: '44px' }}>
            {score == null ? '—' : score}
          </span>
          {score != null && (
            <span className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
              / 100
            </span>
          )}
        </div>
        <span className="inline-flex items-center gap-1.5 t-secondary" style={{ color: 'var(--fg-muted)' }}>
          <span
            aria-hidden
            className="inline-block w-2.5 h-2.5 rounded-full"
            style={{ background: BAND_COLOR[band] }}
          />
          {BAND_WORD[band]}
        </span>
        {lowConfidence && (
          <span className="t-caption" style={{ color: 'var(--health-poor)', fontWeight: 500 }}>
            Under-observed window
          </span>
        )}
      </div>

      {/* Findings by severity */}
      <div className="p-5">
        <span className="t-label" style={{ color: 'var(--fg-muted)' }}>
          Findings by severity
        </span>
        <div className="grid grid-cols-5 gap-3 mt-3">
          {SEVERITY_ORDER.map((sev) => {
            const n = counts[sev];
            const c = SEVERITY_COLORS[sev];
            const active = n > 0;
            return (
              <div key={sev} className="flex flex-col items-start gap-1">
                <span
                  className="t-metric tnum"
                  style={{ color: active ? c.color : 'var(--fg-subtle)', fontSize: 26, lineHeight: '30px' }}
                >
                  {n}
                </span>
                <span
                  className="inline-flex items-center gap-1 t-caption"
                  style={{ color: active ? c.color : 'var(--fg-subtle)' }}
                >
                  <span style={{ color: active ? c.color : 'var(--fg-subtle)' }}>
                    <SeverityGlyph severity={sev} size={9} />
                  </span>
                  {SEVERITY_LABEL[sev]}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>

      {lowConfidence && confidenceNote && (
        <p
          className="t-caption"
          style={{ color: 'var(--fg-muted)', display: 'flex', gap: 6, alignItems: 'baseline' }}
        >
          <span
            aria-hidden
            className="inline-block w-2 h-2 rounded-full shrink-0"
            style={{ background: 'var(--health-poor)', transform: 'translateY(1px)' }}
          />
          {confidenceNote}
        </p>
      )}
    </div>
  );
}
