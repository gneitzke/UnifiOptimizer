import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { formatDuration } from '../shared/format';
import type { Health, JobHealth } from '../../api/types';

/**
 * Collector status chip + source-health panel (dashboard, audit U2). The
 * one-line glance ("Collectors: 6/6 OK · oldest 23h") stays the default view,
 * but it now expands in place into a breakdown of the distinct things that
 * can go quiet without the network itself going quiet: device/client
 * polling, the event socket, whether GET-history is available at all, probe
 * jobs, and when anything was last durably persisted. A user reading "no
 * data" anywhere else in the app should be able to check here whether that
 * means "nothing happened" or "we weren't watching." Status colour is always
 * paired with a word ("OK" / "failing" / "stale"), never colour alone.
 */

const STATUS_COLOR: Record<JobHealth['status'], string> = {
  ok: 'var(--sev-healthy)',
  stale: 'var(--sev-p3)',
  failing: 'var(--sev-p1)',
  UNKNOWN: 'var(--fg-subtle)',
};

const STATUS_RANK: Record<JobHealth['status'], number> = {
  failing: 3,
  stale: 2,
  UNKNOWN: 1,
  ok: 0,
};

function worstStatus(jobs: JobHealth[]): JobHealth['status'] {
  return jobs.reduce<JobHealth['status']>(
    (worst, j) => (STATUS_RANK[j.status] > STATUS_RANK[worst] ? j.status : worst),
    'ok',
  );
}

/** Best-effort split of the opaque job-name list into the categories a user
 * actually thinks about. Job names have no enforced naming scheme (Settings'
 * cadence table renders them raw), so this only ever pulls a job into
 * "polling" or "probes" on a confident name match — everything else stays in
 * the generic bucket rather than being mis-categorized. */
function categorizeJobs(jobs: JobHealth[]) {
  const polling = jobs.filter((j) => /poll|collect|device|client/i.test(j.job));
  const probes = jobs.filter((j) => /probe/i.test(j.job));
  const claimed = new Set([...polling, ...probes].map((j) => j.job));
  const other = jobs.filter((j) => !claimed.has(j.job));
  return { polling, probes, other };
}

function StatusDot({ status }: { status: JobHealth['status'] }) {
  return (
    <span
      aria-hidden
      className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
      style={{ background: STATUS_COLOR[status] }}
    />
  );
}

function JobRow({ job }: { job: JobHealth }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="t-caption flex items-center gap-1.5 min-w-0" style={{ color: 'var(--fg)' }}>
        <StatusDot status={job.status} />
        <span className="truncate">{job.job}</span>
      </span>
      <span className="t-caption tnum shrink-0" style={{ color: 'var(--fg-subtle)' }}>
        {job.last_success_age_s === 'UNKNOWN' ? 'never' : `${formatDuration(job.last_success_age_s)} ago`}
      </span>
    </div>
  );
}

function PanelRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 py-2" style={{ borderTop: '1px solid var(--hairline)' }}>
      <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
        {label}
      </span>
      {children}
    </div>
  );
}

/** Human status word for the WS connection, paired with a dot the same way
 * job status is — never colour alone. `health.websocket.state` is a free-form
 * string from the daemon (e.g. "connected" / "reconnecting" / "closed"); only
 * a recognized-healthy value reads green, everything else reads as
 * attention-worthy rather than assuming it's fine. */
function wsTone(state: string): { color: string; word: string } {
  const s = state.toLowerCase();
  if (s === 'connected' || s === 'open' || s === 'ok') {
    return { color: 'var(--sev-healthy)', word: 'connected' };
  }
  if (s === 'connecting' || s === 'reconnecting') {
    return { color: 'var(--sev-p3)', word: state };
  }
  return { color: 'var(--sev-p1)', word: state || 'unknown' };
}

function SourceHealthPanel({ health }: { health: Health }) {
  const { polling, probes, other } = categorizeJobs(health.jobs);
  const ws = wsTone(health.websocket.state);
  const lastPersisted =
    health.last_persisted_ts ??
    (health.jobs.length
      ? Math.max(
          ...health.jobs
            .map((j) => j.last_success_ts)
            .filter((t): t is number => typeof t === 'number'),
        )
      : null);

  return (
    <div
      role="dialog"
      aria-label="Source health"
      className="absolute left-0 top-[calc(100%+6px)] z-30 flex flex-col rounded-card p-3"
      style={{
        width: 340,
        background: 'var(--elevated)',
        border: '1px solid var(--hairline)',
        boxShadow: 'var(--shadow-elevated)',
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <span className="t-label" style={{ color: 'var(--fg)' }}>
        Source health
      </span>
      <p className="t-caption mt-0.5" style={{ color: 'var(--fg-subtle)' }}>
        What's actually watching the network right now, and when each source last
        checked in.
      </p>

      {polling.length > 0 && (
        <PanelRow label={`Device & client polling (${polling.length})`}>
          {polling.map((j) => (
            <JobRow key={j.job} job={j} />
          ))}
        </PanelRow>
      )}

      <PanelRow label="Event socket">
        <span className="t-caption flex items-center gap-1.5" style={{ color: 'var(--fg)' }}>
          <span
            aria-hidden
            className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
            style={{ background: ws.color }}
          />
          {ws.word}
          {health.websocket.detail && (
            <span style={{ color: 'var(--fg-subtle)' }}>· {health.websocket.detail}</span>
          )}
        </span>
      </PanelRow>

      <PanelRow label="GET-history availability">
        <span className="t-caption" style={{ color: 'var(--fg)' }}>
          {health.backfill}
        </span>
      </PanelRow>

      {probes.length > 0 && (
        <PanelRow label={`Probes (${probes.length})`}>
          {probes.map((j) => (
            <JobRow key={j.job} job={j} />
          ))}
        </PanelRow>
      )}

      {other.length > 0 && (
        <PanelRow label={polling.length > 0 || probes.length > 0 ? 'Other jobs' : 'Collector jobs'}>
          {other.map((j) => (
            <JobRow key={j.job} job={j} />
          ))}
        </PanelRow>
      )}

      <PanelRow label="Last persisted observation">
        <span className="t-caption tnum" style={{ color: 'var(--fg)' }}>
          {lastPersisted != null ? `${formatDuration(health.now - lastPersisted)} ago` : 'never'}
        </span>
      </PanelRow>

      <Link
        to="/settings"
        className="t-caption mt-2 pt-2 hover:underline self-start"
        style={{ color: 'var(--accent)', borderTop: '1px solid var(--hairline)', width: '100%' }}
      >
        Full cadence detail in Settings →
      </Link>
    </div>
  );
}

function ChipShell({
  children,
  dotColor,
  onClick,
  expanded,
}: {
  children: ReactNode;
  dotColor: string;
  onClick?: () => void;
  expanded?: boolean;
}) {
  const inner = (
    <>
      <span
        aria-hidden
        className="inline-block w-2 h-2 rounded-full shrink-0"
        style={{ background: dotColor }}
      />
      <span className="truncate">{children}</span>
      {onClick &&
        (expanded ? (
          <ChevronDown size={13} aria-hidden className="shrink-0" />
        ) : (
          <ChevronRight size={13} aria-hidden className="shrink-0" />
        ))}
    </>
  );
  const cls = 'inline-flex items-center gap-2 px-3 py-1.5 rounded-control t-caption max-w-full';
  if (!onClick) {
    return (
      <span className={cls} style={{ border: '1px solid var(--hairline)', color: 'var(--fg-muted)' }}>
        {inner}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={expanded}
      className={`${cls} transition-colors hover:bg-canvas cursor-pointer`}
      style={{ border: '1px solid var(--hairline)', color: 'var(--fg-muted)' }}
      title="Source health: device/client polling, event socket, GET-history, probes, and persistence"
    >
      {inner}
    </button>
  );
}

export function CollectorStrip({
  health,
  loading,
  error,
}: {
  health: Health | undefined;
  loading: boolean;
  error: boolean;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (error) {
    return (
      <ChipShell dotColor="var(--sev-p1)">Collectors unreachable</ChipShell>
    );
  }

  if (loading && !health) {
    return (
      <div
        className="h-8 w-44 rounded-control animate-pulse"
        style={{ background: 'var(--hairline)' }}
      />
    );
  }

  const jobs = health?.jobs ?? [];
  if (jobs.length === 0) {
    return (
      <ChipShell dotColor="var(--fg-subtle)">No collector jobs registered yet</ChipShell>
    );
  }

  const okCount = jobs.filter((j) => j.status === 'ok').length;
  const failingCount = jobs.filter((j) => j.status === 'failing').length;
  const staleCount = jobs.filter((j) => j.status === 'stale').length;
  const knownAges = jobs
    .map((j) => j.last_success_age_s)
    .filter((a): a is number => typeof a === 'number');
  const oldest = knownAges.length > 0 ? Math.max(...knownAges) : null;

  const detail = [
    failingCount > 0 ? `${failingCount} failing` : null,
    staleCount > 0 ? `${staleCount} stale` : null,
    oldest != null ? `oldest ${formatDuration(oldest)}` : null,
  ]
    .filter((s): s is string => s != null)
    .join(' · ');

  return (
    <div ref={wrapRef} className="relative">
      <ChipShell dotColor={STATUS_COLOR[worstStatus(jobs)]} onClick={() => setOpen((v) => !v)} expanded={open}>
        <span style={{ color: 'var(--fg)' }}>
          Collectors: {okCount}/{jobs.length} OK
        </span>
        {detail ? ` · ${detail}` : ''}
      </ChipShell>
      {open && health && <SourceHealthPanel health={health} />}
    </div>
  );
}
