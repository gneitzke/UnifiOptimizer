import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Check,
  ChevronRight,
  Loader2,
  Minus,
  Play,
  ScrollText,
  X,
} from 'lucide-react';
import { Button, Card, EmptyState } from '../../components/ui';
import { SeverityPill } from '../../components/ui/SeverityPill';
import { ApiError, entityLabel } from '../shared/api';
import { formatDuration, humanizeKey, scoreBand, scoreTo100, sleLabel } from '../shared/format';
import {
  getVisit,
  startVisit,
  type StepStatus,
  type VisitCoverage,
  type VisitIssue,
  type VisitReport,
  type VisitRunSnapshot,
  type VisitSleEntry,
  type VisitStep,
} from './api';

/**
 * /visit — tech-visit mode (docs/ARCHITECTURE.md §3). Kick a one-shot, read-only
 * analysis of a network on arrival, watch its pipeline advance (polled), and read
 * the resulting report: health, issues, topology, and honest data-coverage
 * caveats. The visit runs against a throwaway store, so its issues are a
 * point-in-time snapshot (not linked into the daemon's live issue pages).
 */

const LOOKBACK_OPTIONS = [1, 2, 7];
const SLE_ORDER = ['coverage', 'capacity', 'connect', 'roaming', 'wan', 'infra'];

const BAND_COLOR: Record<ReturnType<typeof scoreBand>, string> = {
  good: 'var(--sev-healthy)',
  fair: 'var(--sev-p3)',
  poor: 'var(--sev-p2)',
  none: 'var(--fg-subtle)',
};
const BAND_WORD: Record<ReturnType<typeof scoreBand>, string> = {
  good: 'Healthy',
  fair: 'Fair',
  poor: 'Degraded',
  none: 'No data',
};

export default function VisitPage() {
  const [snap, setSnap] = useState<VisitRunSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lookback, setLookback] = useState(2);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const poll = useCallback(async () => {
    try {
      const next = await getVisit();
      setSnap(next);
      if (next.status !== 'running') stopPolling();
    } catch {
      // A transient poll failure is non-fatal; keep the last snapshot on screen.
    }
  }, [stopPolling]);

  const startPolling = useCallback(() => {
    stopPolling();
    pollRef.current = window.setInterval(poll, 1000);
  }, [poll, stopPolling]);

  // Initial load: show the last run (if any); resume polling if one is live.
  useEffect(() => {
    let active = true;
    getVisit()
      .then((s) => {
        if (!active) return;
        setSnap(s);
        if (s.status === 'running') startPolling();
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof ApiError ? e.message : String(e));
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
      stopPolling();
    };
  }, [startPolling, stopPolling]);

  const run = useCallback(async () => {
    setStarting(true);
    setError(null);
    try {
      const s = await startVisit(lookback);
      setSnap(s);
      startPolling();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // Already running (e.g. started elsewhere) — just attach to it.
        startPolling();
      } else {
        setError(e instanceof ApiError ? e.message : String(e));
      }
    } finally {
      setStarting(false);
    }
  }, [lookback, startPolling]);

  const status = snap?.status ?? 'idle';
  const running = status === 'running';
  const report = snap?.report ?? null;

  return (
    <div className="px-6 sm:px-8 py-8 mx-auto flex flex-col gap-6" style={{ maxWidth: 900 }}>
      <header>
        <h2 className="t-page-title mb-1" style={{ color: 'var(--fg)' }}>
          Visit
        </h2>
        <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
          An on-demand, read-only analysis run you can watch and hand off as a
          report. The daemon watches continuously; a visit is a point-in-time
          assessment of a network on arrival.
        </p>
      </header>

      <ControlBar
        lookback={lookback}
        setLookback={setLookback}
        onRun={run}
        running={running}
        starting={starting}
      />

      {error && (
        <Card pad="md">
          <span className="t-secondary" style={{ color: 'var(--sev-p2)' }}>
            {error}
          </span>
        </Card>
      )}

      {status === 'failed' && snap?.error && (
        <Card pad="md" className="flex items-start gap-2">
          <X size={16} style={{ color: 'var(--sev-p2)', marginTop: 2, flexShrink: 0 }} />
          <div>
            <div className="t-label" style={{ color: 'var(--fg)' }}>
              The visit run failed
            </div>
            <div className="t-caption mt-0.5" style={{ color: 'var(--fg-muted)' }}>
              {snap.error}
            </div>
          </div>
        </Card>
      )}

      {loading ? null : (running || (snap && snap.steps.length > 0 && !report)) ? (
        <ProgressList steps={snap?.steps ?? []} running={running} />
      ) : report ? (
        <ReportView report={report} runningSteps={snap?.steps ?? []} />
      ) : (
        <Intro />
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Controls                                                                    */
/* -------------------------------------------------------------------------- */
function ControlBar({
  lookback,
  setLookback,
  onRun,
  running,
  starting,
}: {
  lookback: number;
  setLookback: (n: number) => void;
  onRun: () => void;
  running: boolean;
  starting: boolean;
}) {
  return (
    <Card pad="md" className="flex flex-wrap items-center gap-4 justify-between">
      <div className="flex items-center gap-3">
        <span className="t-label" style={{ color: 'var(--fg-muted)' }}>
          Look back
        </span>
        <div
          className="inline-flex rounded-control overflow-hidden"
          style={{ border: '1px solid var(--strong)' }}
          role="group"
          aria-label="Lookback window"
        >
          {LOOKBACK_OPTIONS.map((d, i) => {
            const active = d === lookback;
            return (
              <button
                key={d}
                type="button"
                disabled={running}
                onClick={() => setLookback(d)}
                className="px-3 h-8 t-caption font-medium transition-colors disabled:opacity-40"
                style={{
                  background: active ? 'var(--accent)' : 'transparent',
                  color: active ? 'var(--accent-fg)' : 'var(--fg-muted)',
                  borderLeft: i === 0 ? 'none' : '1px solid var(--strong)',
                }}
              >
                {d === 1 ? '1 day' : `${d} days`}
              </button>
            );
          })}
        </div>
      </div>

      <Button variant="primary" onClick={onRun} disabled={running || starting}>
        {running ? (
          <>
            <Loader2 size={16} className="animate-spin" />
            Running…
          </>
        ) : (
          <>
            <Play size={16} />
            Run visit analysis
          </>
        )}
      </Button>
    </Card>
  );
}

function Intro() {
  const STEPS = [
    {
      title: 'Read-only, on arrival',
      body: 'Connect once, back-fill everything the controller still retains, and analyze it — the daemon’s startup path without the scheduler. Nothing is ever written to the controller.',
    },
    {
      title: 'A full detector pass',
      body: 'Baselines, every detector, and the SLE health model run over the collected window, surfacing configuration and history issues with their evidence.',
    },
    {
      title: 'A report you can hand off',
      body: 'Health, issues, topology, and honest data-coverage caveats — readable on screen and exportable as a self-contained file from the CLI.',
    },
  ];
  return (
    <div className="flex flex-col gap-3">
      <div className="t-label" style={{ color: 'var(--fg-muted)' }}>
        What a visit does
      </div>
      {STEPS.map(({ title, body }) => (
        <Card key={title} className="flex gap-3">
          <span
            className="inline-flex items-center justify-center w-8 h-8 rounded-control shrink-0"
            style={{ background: 'var(--canvas)', color: 'var(--fg-subtle)' }}
            aria-hidden
          >
            <ChevronRight size={16} />
          </span>
          <div>
            <div className="t-label" style={{ color: 'var(--fg)' }}>
              {title}
            </div>
            <div className="t-secondary mt-0.5" style={{ color: 'var(--fg-muted)' }}>
              {body}
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Progress                                                                    */
/* -------------------------------------------------------------------------- */
function StepGlyph({ status }: { status: StepStatus }) {
  if (status === 'running')
    return <Loader2 size={15} className="animate-spin" style={{ color: 'var(--accent)' }} />;
  if (status === 'ok') return <Check size={15} style={{ color: 'var(--sev-healthy)' }} />;
  if (status === 'failed') return <X size={15} style={{ color: 'var(--sev-p2)' }} />;
  if (status === 'skipped')
    return <Minus size={15} style={{ color: 'var(--fg-subtle)' }} />;
  return (
    <span
      aria-hidden
      className="inline-block w-2 h-2 rounded-full"
      style={{ background: 'var(--fg-subtle)', opacity: 0.5 }}
    />
  );
}

function ProgressList({ steps, running }: { steps: VisitStep[]; running: boolean }) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className="t-section" style={{ color: 'var(--fg)' }}>
        {running ? 'Running analysis' : 'Analysis'}
      </h3>
      <Card pad="none">
        <ul>
          {steps.map((s, i) => (
            <li
              key={s.id}
              className="flex items-center gap-3 px-4 py-3"
              style={{
                borderTop: i === 0 ? 'none' : '1px solid var(--hairline)',
              }}
            >
              <span className="w-4 flex justify-center" aria-hidden>
                <StepGlyph status={s.status} />
              </span>
              <span
                className="t-body flex-1"
                style={{
                  color: s.status === 'pending' ? 'var(--fg-subtle)' : 'var(--fg)',
                }}
              >
                {s.label}
              </span>
              {s.detail && (
                <span className="t-caption tnum" style={{ color: 'var(--fg-muted)' }}>
                  {s.detail}
                </span>
              )}
            </li>
          ))}
        </ul>
      </Card>
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Report                                                                      */
/* -------------------------------------------------------------------------- */
function ReportView({
  report,
  runningSteps,
}: {
  report: VisitReport;
  runningSteps: VisitStep[];
}) {
  const headline = scoreTo100(report.headline_score);
  const band = scoreBand(headline);
  const durationS = Math.max(0, report.finished_ts - report.started_ts);
  const openIssues = report.issues.filter((i) => i.state !== 'resolved');
  const sles = report.sles?.sles ?? {};
  const orderedSles = [
    ...SLE_ORDER.filter((k) => sles[k]),
    ...Object.keys(sles).filter((k) => !SLE_ORDER.includes(k)),
  ];
  const failedSteps = runningSteps.filter((s) => s.status === 'failed');

  return (
    <div className="flex flex-col gap-6">
      {/* Headline */}
      <section className="grid gap-4 sm:grid-cols-[minmax(220px,1fr)_1.6fr] items-stretch">
        <Card pad="md" className="flex flex-col justify-between gap-2">
          <span className="t-label" style={{ color: 'var(--fg-muted)' }}>
            Network health
          </span>
          <div className="flex items-baseline gap-2">
            <span className="t-metric" style={{ color: 'var(--fg)' }}>
              {headline == null ? '—' : headline}
            </span>
            <span
              className="inline-flex items-center gap-1.5 t-secondary"
              style={{ color: 'var(--fg-muted)' }}
            >
              <span
                aria-hidden
                className="inline-block w-2.5 h-2.5 rounded-full"
                style={{ background: BAND_COLOR[band] }}
              />
              {BAND_WORD[band]}
            </span>
          </div>
          <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
            {report.controller_host ?? 'controller'} · {report.lookback_days}-day
            window · ran in {formatDuration(durationS)}
          </span>
        </Card>

        <Card pad="md" className="flex items-center gap-6">
          <IssueStat label="Open" value={report.issue_counts.open} />
          <IssueStat label="P1" value={report.issue_counts.p1} tone="p1" />
          <IssueStat label="P2" value={report.issue_counts.p2} tone="p2" />
          <IssueStat label="P3" value={report.issue_counts.p3} tone="p3" />
          <div className="ml-auto text-right">
            <div className="t-metric" style={{ color: 'var(--fg)' }}>
              {report.topology.entity_count}
            </div>
            <div className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
              entities
            </div>
          </div>
        </Card>
      </section>

      {failedSteps.length > 0 && (
        <Card pad="sm" className="flex items-center gap-2">
          <X size={14} style={{ color: 'var(--sev-p2)', flexShrink: 0 }} />
          <span className="t-caption" style={{ color: 'var(--fg-muted)' }}>
            {failedSteps.length} step{failedSteps.length > 1 ? 's' : ''} could not
            complete; the report below is partial (see caveats).
          </span>
        </Card>
      )}

      {/* Service levels */}
      <section className="flex flex-col gap-3">
        <h3 className="t-section" style={{ color: 'var(--fg)' }}>
          Service levels
        </h3>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {orderedSles.map((key) => (
            <SleCard key={key} sleKey={key} entry={sles[key]} />
          ))}
        </div>
      </section>

      {/* Issues */}
      <section className="flex flex-col gap-3">
        <h3 className="t-section" style={{ color: 'var(--fg)' }}>
          Issues{' '}
          {openIssues.length > 0 && (
            <span className="t-caption tnum" style={{ color: 'var(--fg-subtle)' }}>
              {openIssues.length}
            </span>
          )}
        </h3>
        {openIssues.length === 0 ? (
          <Card pad="md">
            <EmptyState
              variant="healthy"
              title="No open issues detected in this window"
            />
          </Card>
        ) : (
          <Card pad="none">
            {openIssues.map((issue, i) => (
              <IssueRow key={issue.id} issue={issue} first={i === 0} />
            ))}
          </Card>
        )}
      </section>

      {/* Topology */}
      <TopologySection report={report} />

      {/* Coverage */}
      {report.coverage.length > 0 && <CoverageSection coverage={report.coverage} />}

      {/* Caveats */}
      {report.caveats.length > 0 && (
        <section className="flex flex-col gap-3">
          <h3 className="t-section" style={{ color: 'var(--fg)' }}>
            Caveats
          </h3>
          <Card pad="md">
            <ul className="flex flex-col gap-2">
              {report.caveats.map((c, i) => (
                <li key={i} className="flex gap-2 t-secondary" style={{ color: 'var(--fg-muted)' }}>
                  <span aria-hidden style={{ color: 'var(--fg-subtle)' }}>
                    •
                  </span>
                  <span>{c}</span>
                </li>
              ))}
            </ul>
          </Card>
        </section>
      )}
    </div>
  );
}

function IssueStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: 'p1' | 'p2' | 'p3';
}) {
  const color =
    tone && value > 0 ? `var(--sev-${tone})` : 'var(--fg)';
  return (
    <div>
      <div className="t-metric tnum" style={{ color }}>
        {value}
      </div>
      <div className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
        {label}
      </div>
    </div>
  );
}

function SleCard({ sleKey, entry }: { sleKey: string; entry: VisitSleEntry }) {
  const score = scoreTo100(entry.score);
  const band = scoreBand(score);
  const offenders = entry.top_offenders.filter((o) => o.fail_minutes > 0).slice(0, 2);
  return (
    <Card pad="sm" className="flex flex-col gap-1.5">
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
            style={{ background: BAND_COLOR[band] }}
          />
          {BAND_WORD[band]}
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
      {score == null ? (
        <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
          No exposed minutes
        </span>
      ) : offenders.length > 0 ? (
        <div className="t-caption" style={{ color: 'var(--fg-muted)' }}>
          on {offenders.map((o) => entityLabel(o.entity)).join(', ')}
        </div>
      ) : (
        <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
          No failed minutes
        </span>
      )}
    </Card>
  );
}

function IssueRow({ issue, first }: { issue: VisitIssue; first: boolean }) {
  const evidence = Object.entries(issue.evidence ?? {})
    .filter(([k, v]) => k !== 'confounders_checked' && typeof v !== 'object')
    .slice(0, 5);
  return (
    <div
      className="flex gap-3 px-4 py-3"
      style={{ borderTop: first ? 'none' : '1px solid var(--hairline)' }}
    >
      <div className="pt-0.5">
        <SeverityPill severity={issue.severity} solid={issue.severity === 'p1'} glyphOnly />
      </div>
      <div className="flex-1 min-w-0">
        <div className="t-body" style={{ color: 'var(--fg)' }}>
          {issue.title}
        </div>
        <div className="t-caption mt-0.5" style={{ color: 'var(--fg-muted)' }}>
          {humanizeKey(issue.detector_key)} · {entityLabel(issue.entity)}
        </div>
        {evidence.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {evidence.map(([k, v]) => (
              <span
                key={k}
                className="inline-flex items-baseline gap-1 px-2 py-0.5 rounded-control t-caption"
                style={{ background: 'var(--canvas)' }}
              >
                <span style={{ color: 'var(--fg-subtle)' }}>{humanizeKey(k)}</span>
                <span className="tnum" style={{ color: 'var(--fg)' }}>
                  {String(v)}
                </span>
              </span>
            ))}
          </div>
        )}
        {issue.confounders.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 mt-2 t-caption" style={{ color: 'var(--fg-subtle)' }}>
            <span>Confounders checked:</span>
            {issue.confounders.map((c) => (
              <span
                key={c}
                className="px-1.5 py-0.5 rounded-control"
                style={{ background: 'var(--canvas)', color: 'var(--fg-muted)' }}
              >
                {humanizeKey(c)}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function TopologySection({ report }: { report: VisitReport }) {
  const { by_type, devices } = report.topology;
  return (
    <section className="flex flex-col gap-3">
      <h3 className="t-section" style={{ color: 'var(--fg)' }}>
        Topology
      </h3>
      <Card pad="md" className="flex flex-col gap-4">
        <div className="flex flex-wrap gap-6">
          {Object.entries(by_type).map(([type, n]) => (
            <div key={type}>
              <div className="t-metric tnum" style={{ color: 'var(--fg)' }}>
                {n}
              </div>
              <div className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
                {humanizeKey(type)}
              </div>
            </div>
          ))}
        </div>
        {devices.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full t-caption" style={{ borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ color: 'var(--fg-subtle)' }}>
                  <th className="text-left font-medium pb-2 pr-4">Device</th>
                  <th className="text-left font-medium pb-2 pr-4">Type</th>
                  <th className="text-left font-medium pb-2 pr-4">Model</th>
                  <th className="text-left font-medium pb-2">MAC</th>
                </tr>
              </thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={d.entity_id} style={{ borderTop: '1px solid var(--hairline)' }}>
                    <td className="py-2 pr-4" style={{ color: 'var(--fg)' }}>
                      {entityLabel(d)}
                    </td>
                    <td className="py-2 pr-4" style={{ color: 'var(--fg-muted)' }}>
                      {humanizeKey(String(d.type ?? ''))}
                    </td>
                    <td className="py-2 pr-4 tnum" style={{ color: 'var(--fg-muted)' }}>
                      {d.model ?? '—'}
                    </td>
                    <td className="py-2 tnum" style={{ color: 'var(--fg-muted)' }}>
                      {d.native_id ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </section>
  );
}

function pct(frac: number | null): string {
  if (frac == null) return '—';
  return `${Math.round(frac * 100)}%`;
}

function CoverageSection({ coverage }: { coverage: VisitCoverage[] }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <ScrollText size={16} style={{ color: 'var(--fg-subtle)' }} aria-hidden />
        <h3 className="t-section" style={{ color: 'var(--fg)' }}>
          Data coverage
        </h3>
      </div>
      <Card pad="md">
        <p className="t-caption mb-3" style={{ color: 'var(--fg-subtle)' }}>
          Live is what a poller collected in-window; backfill is reconstructed from
          controller history (coarser, weighted down by detectors).
        </p>
        <div className="overflow-x-auto">
          <table className="w-full t-caption" style={{ borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: 'var(--fg-subtle)' }}>
                <th className="text-left font-medium pb-2 pr-4">Job</th>
                <th className="text-right font-medium pb-2 pr-4">Live</th>
                <th className="text-right font-medium pb-2 pr-4">Backfill</th>
                <th className="text-right font-medium pb-2">Total</th>
              </tr>
            </thead>
            <tbody>
              {coverage.map((c) => (
                <tr key={c.job} style={{ borderTop: '1px solid var(--hairline)' }}>
                  <td className="py-2 pr-4" style={{ color: 'var(--fg)' }}>
                    {humanizeKey(c.job)}
                  </td>
                  <td className="py-2 pr-4 text-right tnum" style={{ color: 'var(--fg-muted)' }}>
                    {pct(c.live)}
                  </td>
                  <td className="py-2 pr-4 text-right tnum" style={{ color: 'var(--fg-muted)' }}>
                    {pct(c.backfill)}
                  </td>
                  <td className="py-2 text-right tnum" style={{ color: 'var(--fg-muted)' }}>
                    {pct(c.total)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </section>
  );
}
