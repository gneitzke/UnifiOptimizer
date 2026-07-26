import { Card } from '../../components/ui/Card';
import { formatDuration } from '../shared/format';
import type { Health, JobHealth } from '../../api/types';

/**
 * Collector health strip: last-poll age per job straight from `/api/health`, with
 * honest UNKNOWN when the daemon has no successful poll on record (never a faked
 * "0s ago"). Status maps to a shape-paired dot so it survives colorblindness.
 */

const STATUS_META: Record<
  JobHealth['status'],
  { color: string; label: string }
> = {
  ok: { color: 'var(--sev-healthy)', label: 'OK' },
  stale: { color: 'var(--sev-p3)', label: 'Stale' },
  failing: { color: 'var(--sev-p1)', label: 'Failing' },
  UNKNOWN: { color: 'var(--fg-subtle)', label: 'Unknown' },
};

function ageLabel(age: JobHealth['last_success_age_s']): string {
  if (age === 'UNKNOWN' || age == null) return 'UNKNOWN';
  return `${formatDuration(age)} ago`;
}

function JobChip({ job }: { job: JobHealth }) {
  const meta = STATUS_META[job.status] ?? STATUS_META.UNKNOWN;
  const unknown = job.last_success_age_s === 'UNKNOWN';
  return (
    <div
      className="flex items-center gap-2 px-3 py-2 rounded-control shrink-0"
      style={{ border: '1px solid var(--hairline)', background: 'var(--canvas)' }}
      title={
        job.consecutive_failures > 0
          ? `${job.consecutive_failures} consecutive failure(s)`
          : undefined
      }
    >
      <span
        aria-hidden
        className="inline-block w-2 h-2 rounded-full shrink-0"
        style={{ background: meta.color }}
      />
      <div className="flex flex-col leading-tight">
        <span className="t-caption" style={{ color: 'var(--fg)' }}>
          {job.job}
        </span>
        <span className="t-micro tnum" style={{ color: 'var(--fg-muted)' }}>
          {/* Status word (not color alone — never-do rule 2), then age. When the
              job has never run, the word already says "Unknown"; don't repeat it
              as a redundant "· UNKNOWN" age. */}
          <span style={{ color: meta.color }}>{meta.label}</span>
          {unknown ? null : (
            <span style={{ color: 'var(--fg-muted)' }}>
              {' · '}
              {ageLabel(job.last_success_age_s)}
              {job.consecutive_failures > 0 ? ` · ${job.consecutive_failures} fail` : ''}
            </span>
          )}
        </span>
      </div>
    </div>
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
  return (
    <Card pad="sm" className="flex flex-col gap-2 min-w-0">
      <div className="flex items-baseline justify-between">
        <span className="t-label" style={{ color: 'var(--fg-muted)' }}>
          Collectors
        </span>
        {health && (
          <span className="t-caption" style={{ color: 'var(--fg-muted)' }}>
            backfill: {health.backfill}
          </span>
        )}
      </div>

      {error ? (
        <div className="flex items-center gap-2 py-1 t-caption" style={{ color: 'var(--fg-muted)' }}>
          <span
            aria-hidden
            className="inline-block w-2 h-2 rounded-full"
            style={{ background: 'var(--sev-p1)' }}
          />
          Daemon unreachable — collector status unknown
        </div>
      ) : loading && !health ? (
        <div className="flex gap-2">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-11 w-32 rounded-control animate-pulse"
              style={{ background: 'var(--hairline)' }}
            />
          ))}
        </div>
      ) : health && health.jobs.length > 0 ? (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {health.jobs.map((j) => (
            <JobChip key={j.job} job={j} />
          ))}
        </div>
      ) : (
        <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
          No collector jobs registered yet
        </span>
      )}
    </Card>
  );
}
