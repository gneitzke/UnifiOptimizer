import { useId, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Card } from '../../components/ui/Card';
import { TimeSeriesChart } from '../../components/ui/TimeSeriesChart';
import type { ChartPoint } from '../../components/ui/chart-utils';
import { fmt } from '../../components/ui/chart-utils';
import { EntityLink } from '../shared/EntityLink';
import { humanizeKey, scoreBand, scoreTo100, sleLabel } from '../shared/format';
import type { SleEntryRow } from '../shared/api';

/**
 * One SLE's health block (dashboard). Shows the score, a fixed 0-100 trend of the
 * window, and an inline-expandable classifier breakdown + offenders — inline
 * expand is the right pattern for short comparison detail read across many blocks
 * (docs §Interaction). Green is used only for a genuinely-healthy score (this IS
 * a health signal); a poor score gets a neutral treatment, never a painted card.
 */

const BAND_META: Record<
  ReturnType<typeof scoreBand>,
  { color: string; word: string }
> = {
  good: { color: 'var(--sev-healthy)', word: 'Good' },
  fair: { color: 'var(--sev-p3)', word: 'Fair' },
  poor: { color: 'var(--sev-p2)', word: 'Poor' },
  none: { color: 'var(--fg-subtle)', word: 'No data' },
};

/** Turn the SLE trend into chart points, inserting null gaps where the backend
 *  omitted no-data buckets (so a discontinuity draws as a gap, never a bridge).
 *
 *  The gap threshold is the SERVER's bucket cadence (`window / buckets`), NOT the
 *  average spacing of the returned samples. With only a few sparse samples the
 *  latter is huge, so a multi-hour no-data span never trips a break and two far
 *  points get bridged into a smooth (invented) ramp. Anchoring to the real bucket
 *  width means any hole wider than ~1.5 buckets breaks the line; isolated readings
 *  then render as dots with honest gaps, never a fabricated decline. */
function toChartPoints(
  ts: SleEntryRow['timeseries'],
  startTs: number,
  endTs: number,
  buckets: number,
): ChartPoint[] {
  if (ts.length === 0) return [];
  const cadence = Math.max(1, (endTs - startTs) / Math.max(1, buckets));
  const threshold = cadence * 1.5;
  const out: ChartPoint[] = [];
  let prev: number | null = null;
  for (const p of ts) {
    if (prev != null && p.ts - prev > threshold) {
      out.push({ ts: prev + cadence, value: null });
    }
    out.push({ ts: p.ts, value: Math.round(p.score * 100) });
    prev = p.ts;
  }
  return out;
}

export function SleHealthBlock({
  sleKey,
  entry,
  startTs,
  endTs,
  buckets,
}: {
  sleKey: string;
  entry: SleEntryRow;
  startTs: number;
  endTs: number;
  /** The server bucket count the trend was requested with (drives gap cadence). */
  buckets: number;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

  const score = scoreTo100(entry.score);
  const band = BAND_META[scoreBand(score)];
  const points = toChartPoints(entry.timeseries, startTs, endTs, buckets);

  // Failing classifiers only (drop 'ok'), most minutes first.
  const failClassifiers = Object.entries(entry.classifiers)
    .filter(([k]) => k !== 'ok')
    .sort((a, b) => b[1] - a[1]);
  const maxFail = failClassifiers.reduce((m, [, v]) => Math.max(m, v), 0);
  const offenders = entry.top_offenders.filter((o) => o.fail_minutes > 0);
  const hasBreakdown = failClassifiers.length > 0 || offenders.length > 0;

  return (
    <Card pad="sm" className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between">
        <span className="t-label" style={{ color: 'var(--fg-muted)' }}>
          {sleLabel(sleKey)}
        </span>
        <span
          className="inline-flex items-center gap-1.5 t-caption"
          style={{ color: 'var(--fg-muted)' }}
        >
          <span
            aria-hidden
            className="inline-block w-2 h-2 rounded-full"
            style={{ background: band.color }}
          />
          {band.word}
        </span>
      </div>

      <div className="flex items-baseline gap-1">
        <span className="t-metric" style={{ color: 'var(--fg)' }}>
          {score == null ? '—' : score}
        </span>
        {score != null && (
          <span className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
            / 100
          </span>
        )}
      </div>

      {points.length > 0 ? (
        <TimeSeriesChart
          series={[{ name: sleLabel(sleKey), points, fill: true }]}
          height={92}
          percentage
          contextLabel="Score, last 24h"
        />
      ) : (
        <div
          className="h-[92px] flex items-center justify-center t-caption"
          style={{ color: 'var(--fg-subtle)' }}
        >
          No exposed minutes in this window
        </div>
      )}

      {hasBreakdown ? (
        <>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls={panelId}
            className="inline-flex items-center gap-1 t-caption self-start cursor-pointer hover:underline"
            style={{ color: 'var(--accent)' }}
          >
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            {open ? 'Hide breakdown' : 'Why'}
          </button>

          {open && (
            <div
              id={panelId}
              className="flex flex-col gap-2 pt-1"
              style={{ borderTop: '1px solid var(--hairline)' }}
            >
              {failClassifiers.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  {failClassifiers.map(([name, minutes]) => (
                    <div key={name} className="flex flex-col gap-0.5">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="t-caption" style={{ color: 'var(--fg)' }}>
                          {humanizeKey(name)}
                        </span>
                        <span
                          className="t-caption tnum"
                          style={{ color: 'var(--fg-muted)' }}
                        >
                          {fmt(minutes, minutes < 10 ? 1 : 0)} fail-min
                        </span>
                      </div>
                      <div
                        className="h-1 rounded-full"
                        style={{ background: 'var(--hairline)' }}
                      >
                        <div
                          className="h-1 rounded-full"
                          style={{
                            width: `${maxFail ? (minutes / maxFail) * 100 : 0}%`,
                            background: 'var(--sev-p2)',
                          }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {offenders.length > 0 && (
                <div className="flex flex-col gap-1">
                  <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
                    Attributed to
                  </span>
                  {offenders.map((o, i) => (
                    <div
                      key={o.attributed_entity_id ?? `none-${i}`}
                      className="flex items-baseline justify-between gap-2"
                    >
                      {o.entity ? (
                        <EntityLink entity={o.entity} />
                      ) : (
                        <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
                          Unattributed
                        </span>
                      )}
                      <span
                        className="t-caption tnum"
                        style={{ color: 'var(--fg-muted)' }}
                      >
                        {fmt(o.fail_minutes, o.fail_minutes < 10 ? 1 : 0)} min
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      ) : score != null ? (
        <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
          No failed minutes in this window
        </span>
      ) : null}
    </Card>
  );
}
