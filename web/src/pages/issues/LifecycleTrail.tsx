import { useMemo } from 'react';
import { RelativeTime } from '../../components/ui/RelativeTime';
import { humanizeKey } from '../shared/format';
import type { IssueEventRow } from '../shared/api';

/**
 * The issue's full lifecycle trail (§7: every transition writes an issue_events
 * row; nothing is untraceable). A vertical, chronological timeline — detected at
 * the top, down to the latest transition — with each event's compact detail. The
 * dot marks meaning by kind; color is used sparingly (green only for a resolved
 * milestone, since resolution is genuinely a good outcome, not a severity).
 */

const KIND_COLOR: Record<string, string> = {
  detected: 'var(--fg-subtle)',
  escalated: 'var(--sev-p2)',
  reopened: 'var(--sev-p2)',
  acked: 'var(--accent)',
  snoozed: 'var(--fg-subtle)',
  fix_proposed: 'var(--accent)',
  fix_applied: 'var(--accent)',
  fix_verified: 'var(--sev-healthy)',
  fix_failed: 'var(--sev-p1)',
  resolved: 'var(--sev-healthy)',
  investigated: 'var(--accent)',
};

function detailSummary(detail: Record<string, unknown>): string | null {
  const entries = Object.entries(detail).filter(([, v]) => v != null && v !== '');
  if (entries.length === 0) return null;
  return entries
    .map(([k, v]) => `${humanizeKey(k)}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(' · ');
}

export function LifecycleTrail({ events }: { events: IssueEventRow[] }) {
  const ordered = useMemo(
    () => [...events].sort((a, b) => a.ts - b.ts || a.id - b.id),
    [events],
  );

  if (ordered.length === 0) {
    return (
      <p className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
        No lifecycle events recorded.
      </p>
    );
  }

  return (
    <ol className="flex flex-col">
      {ordered.map((ev, i) => {
        const color = KIND_COLOR[ev.kind] ?? 'var(--fg-subtle)';
        const summary = detailSummary(ev.detail);
        const last = i === ordered.length - 1;
        return (
          <li key={ev.id} className="flex gap-3">
            {/* Rail */}
            <div className="flex flex-col items-center shrink-0" style={{ width: 12 }}>
              <span
                className="inline-block w-2.5 h-2.5 rounded-full mt-1.5"
                style={{ background: color, border: '2px solid var(--surface)', boxShadow: `0 0 0 1px ${color}` }}
              />
              {!last && <span className="flex-1 w-px my-1" style={{ background: 'var(--hairline)' }} />}
            </div>
            {/* Body */}
            <div className={last ? 'pb-0.5' : 'pb-4'}>
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="t-label" style={{ color: 'var(--fg)' }}>
                  {humanizeKey(ev.kind)}
                </span>
                <RelativeTime
                  ts={ev.ts}
                  mode="relative"
                  className="t-caption tnum"
                />
                <span className="t-micro tnum" style={{ color: 'var(--fg-subtle)' }}>
                  <RelativeTime ts={ev.ts} mode="as-of" />
                </span>
              </div>
              {summary && (
                <div className="t-caption mt-0.5" style={{ color: 'var(--fg-muted)' }}>
                  {summary}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
