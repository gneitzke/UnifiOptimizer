import { Fragment, type ReactNode, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import type { IssueState } from '../../api/types';
import { RelativeTime } from '../../components/ui/RelativeTime';
import { humanizeKey } from '../shared/format';
import type { IssueEventRow } from '../shared/api';

/**
 * The issue's full lifecycle trail (§7: every transition writes an issue_events
 * row; nothing is untraceable). A vertical, chronological timeline — detected at
 * the top, down to the latest transition — with each event's compact detail. The
 * dot marks meaning by kind; color is used sparingly (green only for a resolved
 * milestone, since resolution is genuinely a good outcome, not a severity).
 *
 * Each entry's detail renders as a plain-English sentence (Gitea #18 item 3),
 * keyed on `kind` — the fixed, small vocabulary the issue engine writes
 * (`netadmin.issues.models.EventKind`), shared by every detector, so this needs
 * no per-detector authoring. A `fix_applied`/`fix_proposed` change id links to
 * the change ledger (item 4). The timestamp keeps its exact HH:MM:SS *and* its
 * date once the event isn't from today, since an issue can span midnight.
 */

const KIND_COLOR: Record<string, string> = {
  detected: 'var(--fg-subtle)',
  escalated: 'var(--sev-p2)',
  resolving: 'var(--accent)',
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

const FIX_FAILED_REASON: Record<string, string> = {
  still_firing_after_window: 'the issue kept firing after the verification window',
  refire_during_resolving: 'it recurred while resolving',
  refire_after_apply: 'it recurred right after the fix was applied',
};

function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? '' : 's'}`;
}

function actionLabel(detail: Record<string, unknown>): string | null {
  return typeof detail.action === 'string' && detail.action ? humanizeKey(detail.action) : null;
}

/** Change ledger id(s) out of a fix_proposed/fix_applied detail blob, in
 * whichever shape the caller wrote it: the real fix engine's `change_ids`
 * array, or a singular `change_id` (older/simplified callers). */
function changeIds(detail: Record<string, unknown>): number[] {
  const many = detail.change_ids;
  if (Array.isArray(many)) return many.filter((v): v is number => typeof v === 'number');
  const one = detail.change_id;
  return typeof one === 'number' ? [one] : [];
}

function ChangeLinks({ ids }: { ids: number[] }) {
  if (ids.length === 0) return null;
  return (
    <>
      {' ('}
      {ids.map((id, i) => (
        <Fragment key={id}>
          {i > 0 && ', '}
          <Link to={`/changes?id=${id}`} className="hover:underline" style={{ color: 'var(--accent)' }}>
            change #{id}
          </Link>
        </Fragment>
      ))}
      {')'}
    </>
  );
}

/** A plain-English one-liner for one event's `detail`, or a generic key:value
 * fallback for a shape this hasn't been taught — an unfamiliar event still
 * shows something, never blank. */
function eventSummary(kind: string, detail: Record<string, unknown>): ReactNode {
  switch (kind) {
    case 'detected': {
      const sev = typeof detail.severity === 'string' ? detail.severity.toUpperCase() : null;
      return sev ? `Opened at ${sev} severity.` : null;
    }
    case 'escalated': {
      if (detail.reason === 'm_reached') {
        const n = typeof detail.occurrences === 'number' ? detail.occurrences : null;
        return n != null ? `Escalated after ${plural(n, 'occurrence')}.` : 'Escalated.';
      }
      if (detail.reason === 'refire_during_resolving') {
        return 'Escalated: it recurred while resolving.';
      }
      return 'Escalated.';
    }
    case 'resolving': {
      // A departed client is not a clean check. Nothing was observed at all, so
      // saying "clean" here would describe evidence that does not exist.
      if (detail.reason === 'entity_absent') {
        return 'The client left the network; clearing.';
      }
      const k = typeof detail.k === 'number' ? detail.k : null;
      return k != null
        ? `Clean on the first check since it fired. Resolves once it stays clear through ${plural(k, 'consecutive check')}.`
        : 'Clean on the first check since it fired.';
    }
    case 'reopened':
      return 'Reopened: it recurred within 24 hours of resolving.';
    case 'acked':
      return 'Acknowledged.';
    case 'snoozed': {
      const until = typeof detail.until_ts === 'number' ? detail.until_ts : null;
      return until != null ? (
        <>
          Snoozed until <RelativeTime ts={until} mode="at" />.
        </>
      ) : (
        'Snoozed.'
      );
    }
    case 'fix_proposed': {
      const action = actionLabel(detail);
      const from = detail.from;
      const to = detail.to;
      const change = from !== undefined && to !== undefined ? ` (${String(from)} → ${String(to)})` : '';
      return action ? `Fix proposed: ${action}${change}.` : `Fix proposed.${change}`;
    }
    case 'fix_applied': {
      const action = actionLabel(detail);
      return (
        <>
          {action ? `Fix applied: ${action}.` : 'Fix applied.'}
          <ChangeLinks ids={changeIds(detail)} />
        </>
      );
    }
    case 'fix_verified':
      return 'Fix verified: the issue stayed clear through the verification window.';
    case 'fix_failed': {
      const reason = typeof detail.reason === 'string' ? FIX_FAILED_REASON[detail.reason] : null;
      return reason ? `Fix failed: ${reason}.` : 'Fix failed.';
    }
    case 'resolved': {
      if (detail.reason === 'entity_absent') {
        return 'Resolved: the client left the network.';
      }
      const n = typeof detail.clear_streak === 'number' ? detail.clear_streak : null;
      return n != null ? `Resolved after ${plural(n, 'clean check')}.` : 'Resolved.';
    }
    case 'investigated': {
      const provider = typeof detail.provider === 'string' ? detail.provider : null;
      return provider ? `Investigated via ${provider}.` : 'Investigated.';
    }
    default:
      return fallbackSummary(detail);
  }
}

function fallbackSummary(detail: Record<string, unknown>): string | null {
  const entries = Object.entries(detail).filter(([, v]) => v != null && v !== '');
  if (entries.length === 0) return null;
  return entries
    .map(([k, v]) => `${humanizeKey(k)}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(' · ');
}

export function LifecycleTrail({
  events,
  currentState,
  clearStreak,
}: {
  events: IssueEventRow[];
  /** The issue's live state (docs §12): while it's still `resolving`, a trailing
   * progress row appends below the trail — the `resolving` event only ever
   * fires once (on the first clean check), so without this the trail goes
   * stale the moment a second/third clean check lands, even though the issue
   * is still actively clearing. */
  currentState?: IssueState;
  /** The issue's live `clear_streak` (IssueRow), paired with the `k` the
   * `resolving` event recorded to render "N of K" without a new event per tick. */
  clearStreak?: number;
}) {
  const ordered = useMemo(
    () => [...events].sort((a, b) => a.ts - b.ts || a.id - b.id),
    [events],
  );

  const resolvingK = useMemo(() => {
    for (let i = ordered.length - 1; i >= 0; i--) {
      const ev = ordered[i];
      if (ev.kind === 'resolving' && typeof ev.detail.k === 'number') {
        return ev.detail.k as number;
      }
    }
    return null;
  }, [ordered]);

  if (ordered.length === 0) {
    return (
      <p className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
        No lifecycle events recorded.
      </p>
    );
  }

  const showProgress = currentState === 'resolving' && clearStreak != null && clearStreak > 0;

  return (
    <ol className="flex flex-col">
      {ordered.map((ev, i) => {
        const color = KIND_COLOR[ev.kind] ?? 'var(--fg-subtle)';
        const summary = eventSummary(ev.kind, ev.detail);
        const last = i === ordered.length - 1 && !showProgress;
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
                  <RelativeTime ts={ev.ts} mode="exact" prefix="as of " />
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
      {showProgress && (
        <li className="flex gap-3">
          <div className="flex flex-col items-center shrink-0" style={{ width: 12 }}>
            <Loader2
              size={10}
              strokeWidth={3}
              className="mt-1.5 animate-spin"
              style={{ color: 'var(--accent)' }}
            />
          </div>
          <div className="pb-0.5">
            <span className="t-label" style={{ color: 'var(--fg)' }}>
              Still clearing
            </span>
            <div className="t-caption mt-0.5" style={{ color: 'var(--fg-muted)' }}>
              {resolvingK != null
                ? `${plural(clearStreak as number, 'consecutive clean check')} so far, of ${resolvingK} needed.`
                : `${plural(clearStreak as number, 'consecutive clean check')} so far.`}
            </div>
          </div>
        </li>
      )}
    </ol>
  );
}
