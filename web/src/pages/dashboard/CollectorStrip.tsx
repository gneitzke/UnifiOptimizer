import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { formatDuration } from '../shared/format';
import type { Health, JobHealth } from '../../api/types';

/**
 * Collector status chip (dashboard). A one-line glance ("Collectors: 6/6 OK
 * · oldest 23h") that links to Settings' full per-job cadence table — the
 * dashboard's job is answering "is anything wrong", not exposing internal
 * job names (`detect_fast`, `events_catchup`, ...) at equal billing with
 * network health. Status colour is always paired with a word ("OK" /
 * "failing" / "stale"), never colour alone.
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

function ChipShell({
  children,
  dotColor,
  linked = true,
}: {
  children: ReactNode;
  dotColor: string;
  linked?: boolean;
}) {
  const inner = (
    <>
      <span
        aria-hidden
        className="inline-block w-2 h-2 rounded-full shrink-0"
        style={{ background: dotColor }}
      />
      <span className="truncate">{children}</span>
      {linked && <ChevronRight size={13} aria-hidden className="shrink-0" />}
    </>
  );
  const cls = 'inline-flex items-center gap-2 px-3 py-1.5 rounded-control t-caption max-w-full';
  if (!linked) {
    return (
      <span className={cls} style={{ border: '1px solid var(--hairline)', color: 'var(--fg-muted)' }}>
        {inner}
      </span>
    );
  }
  return (
    <Link
      to="/settings"
      className={`${cls} transition-colors hover:bg-canvas`}
      style={{ border: '1px solid var(--hairline)', color: 'var(--fg-muted)' }}
      title="Collector job cadence and last-success detail on Settings"
    >
      {inner}
    </Link>
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
  if (error) {
    return (
      <ChipShell dotColor="var(--sev-p1)" linked={false}>
        Collectors unreachable
      </ChipShell>
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
    <ChipShell dotColor={STATUS_COLOR[worstStatus(jobs)]}>
      <span style={{ color: 'var(--fg)' }}>
        Collectors: {okCount}/{jobs.length} OK
      </span>
      {detail ? ` · ${detail}` : ''}
    </ChipShell>
  );
}
