import { Activity } from 'lucide-react';
import { Card } from '../../components/ui/Card';

/**
 * First-run "we're collecting" notice for a freshly connected daemon.
 *
 * A brand-new connect has no history yet, so the dashboard's data panels are
 * legitimately empty. DESIGN_FOUNDATION's empty-state rule: distinguish "no data
 * yet (setup guidance)" from a broken blank. This states the honest truth — the
 * daemon is reading now, the first read lands in minutes, and a week of history is
 * backfilling from the controller — instead of leaving the page looking broken.
 *
 * It is not an error or a severity signal (neutral accent, no red/green), and it
 * self-retires: it only renders while there are zero scored service levels.
 */

/** Backfill state from `/api/health` (`state.backfill_status`). */
type Backfill = string | undefined;

function backfillLine(backfill: Backfill): string {
  switch (backfill) {
    case 'done':
      return 'About a week of history has been backfilled from your controller.';
    case 'failed':
      return "Backfilling older history didn't complete; live readings still collect normally.";
    case 'absent':
      return 'History builds up as readings collect.';
    default:
      // pending | running | unknown
      return 'About a week of history is backfilling from your controller.';
  }
}

export function FirstRunNotice({ backfill }: { backfill?: string }) {
  return (
    <Card pad="md" className="flex items-start gap-3">
      <span
        aria-hidden
        className="mt-0.5 inline-flex items-center justify-center w-8 h-8 rounded-control shrink-0"
        style={{
          background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
          color: 'var(--accent)',
        }}
      >
        <Activity size={17} />
      </span>
      <div className="flex flex-col gap-1 min-w-0">
        <span className="t-label" style={{ color: 'var(--fg)' }}>
          Connected. Collecting now.
        </span>
        <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
          Your first read appears in a few minutes. {backfillLine(backfill)} Scores and issues fill
          in as data arrives — nothing to do but wait.
        </p>
      </div>
    </Card>
  );
}
