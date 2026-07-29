import { BellOff } from 'lucide-react';
import type { IssueRow } from './api';
import { isSuppressedNow, suppressionNote } from './format';

/**
 * The neutral marker that an issue's claim on attention is parked (Gitea #49).
 * One source, shared by every surface that shows a suppressed issue in situ — the
 * Issues list rows, the incident group expander, and the incident detail member
 * rows — so the badge cannot drift into a second pattern.
 *
 * Same construction as RecurrenceBadge: a BellOff glyph on a neutral fill, never
 * a severity tint, because a suppressed issue's *facts* do not fade (its Sev pill
 * is unchanged); only its attention claim is set aside. The hover/sr sentence
 * carries the load-bearing "measured impact is unchanged". Renders nothing unless
 * the issue is suppressed *right now* (the derived rule), so a caller can drop it
 * in unconditionally.
 */
export function SuppressedBadge({ issue, now }: { issue: IssueRow; now: number }) {
  if (!isSuppressedNow(issue, now)) return null;
  const note = suppressionNote(issue, now);
  return (
    <span
      className="relative inline-flex items-center gap-0.5 shrink-0 h-[18px] px-1.5 rounded-full t-micro"
      style={{ background: 'var(--sev-neutral-fill)', color: 'var(--fg-muted)' }}
      title={note}
    >
      <BellOff size={10} strokeWidth={2.5} aria-hidden />
      <span aria-hidden>Suppressed</span>
      <span className="sr-only">{note}</span>
    </span>
  );
}
