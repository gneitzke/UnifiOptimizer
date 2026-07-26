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
 * a health signal) or a confirmed quiet pass; a poor score gets a neutral
 * treatment, never a painted card.
 *
 * Confidence is a first-class state, not an afterthought. The backend
 * (netadmin.sle.scores / the /api/sle router) already resolves four distinct
 * situations a bare `score` cannot tell apart on its own — see `sleUiState`:
 * a real, confidently-measured score; a real score built from too little
 * exposure to headline (`below_floor`); a positively-confirmed "nothing
 * happened" quiet pass (`quiet_pass`); and "cannot be measured on this
 * controller" (`measurable: false`, e.g. connect while the event stream is
 * down). Collapsing all four into "no exposed minutes in this window" is
 * exactly the "no data reads as scoring badly" bug this block exists to avoid.
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

const NEUTRAL_BADGE = { color: 'var(--fg-subtle)', word: 'Insufficient data' };
const UNAVAILABLE_BADGE = { color: 'var(--fg-subtle)', word: 'Unavailable' };
const QUIET_BADGE = { color: 'var(--sev-healthy)', word: 'Quiet' };

/** Per-occurrence SLEs read better as "no roams" than the generic "no <sle>
 *  events"; everything else falls back to a label built from its name. */
const QUIET_PASS_COPY: Record<string, string> = {
  roaming: 'No roams',
  connect: 'No connection events',
};

function capitalize(s: string): string {
  return s.length ? s[0].toUpperCase() + s.slice(1) : s;
}

/** Compact window phrase ("24h", "7d", "45m") for captions — derived from the
 *  actual query span rather than a hardcoded "24h", so a future window change
 *  never leaves stale text behind. */
function windowLabel(seconds: number): string {
  if (seconds >= 86_400 && seconds % 86_400 === 0) {
    const days = seconds / 86_400;
    return days === 1 ? '24h' : `${days}d`;
  }
  if (seconds >= 3_600) return `${Math.round(seconds / 3_600)}h`;
  return `${Math.round(Math.max(1, seconds / 60))}m`;
}

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

/** The four exposure states the /api/sle payload already resolves; see the
 *  module docstring. Priority: an explicit "cannot measure" reason always wins
 *  over a bare absence of data, and a confirmed quiet pass always wins over the
 *  generic "insufficient data" reading of the same (score === null) fact. */
type SleUiState = 'measured' | 'below_floor' | 'quiet_pass' | 'not_measurable' | 'no_data';

function sleUiState(entry: SleEntryRow): SleUiState {
  if (!entry.measurable) return 'not_measurable';
  if (entry.quiet_pass) return 'quiet_pass';
  if (entry.score == null) return 'no_data';
  if (entry.below_floor) return 'below_floor';
  return 'measured';
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

  const state = sleUiState(entry);
  const scoreValue = scoreTo100(entry.score);
  const confident = state === 'measured';
  // The big number and the confident Good/Fair/Poor band are suppressed for
  // every state except a fully-confident score — a real-but-thin number (below
  // the floor) is exactly as misleading as a fabricated one if it is painted
  // with full confidence (docs: "no data must never look like scoring badly").
  const displayScore = confident ? scoreValue : null;
  const badge =
    state === 'measured'
      ? BAND_META[scoreBand(scoreValue)]
      : state === 'quiet_pass'
        ? QUIET_BADGE
        : state === 'not_measurable'
          ? UNAVAILABLE_BADGE
          : NEUTRAL_BADGE;

  const points = toChartPoints(entry.timeseries, startTs, endTs, buckets);
  const span = windowLabel(Math.max(1, endTs - startTs));
  const fullyMeasured = entry.evaluated_buckets >= entry.window_buckets;
  const contextLabel = fullyMeasured
    ? `Score, last ${span}`
    : `Measured ${entry.evaluated_buckets} of ${entry.window_buckets} intervals · last ${span}`;

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
            style={{ background: badge.color }}
          />
          {badge.word}
        </span>
      </div>

      <div className="flex items-baseline gap-1">
        <span className="t-metric" style={{ color: 'var(--fg)' }}>
          {displayScore == null ? '—' : displayScore}
        </span>
        {displayScore != null && (
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
          contextLabel={contextLabel}
        />
      ) : (
        <div
          className="h-[92px] flex flex-col items-center justify-center gap-1 text-center px-2"
          style={{ color: 'var(--fg-subtle)' }}
        >
          {state === 'not_measurable' ? (
            <span className="t-caption" style={{ color: 'var(--fg-muted)' }}>
              {capitalize(entry.unmeasurable_reason ?? 'Not measurable on this controller')}
            </span>
          ) : state === 'quiet_pass' ? (
            <span className="t-caption" style={{ color: 'var(--fg-muted)' }}>
              {(QUIET_PASS_COPY[sleKey] ?? `No ${sleLabel(sleKey).toLowerCase()} events`)}{' '}
              in last {span}
            </span>
          ) : (
            <span className="t-caption">
              Insufficient data: measured {entry.evaluated_buckets} of {entry.window_buckets}{' '}
              intervals
            </span>
          )}
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
      ) : entry.score != null ? (
        <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
          No failed minutes in this window
        </span>
      ) : null}
    </Card>
  );
}
