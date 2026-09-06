import { Check, CircleHelp, Loader2, RotateCcw, X } from 'lucide-react';
import { cn } from '../../components/ui/cn';
import { CHANGE_STATUS_META, normalizeChangeStatus, type ChangeStatus } from './changeStatusMeta';

/**
 * Shared change/fix-attempt status pill (audit U5). A fix send is no longer a
 * binary applied/reverted — the daemon can also report that it is still in
 * flight, that it failed outright, or that the send was ambiguous (the
 * request may or may not have reached the device). Every surface that shows
 * an applied-change row — ProposedFix, the Changes ledger, and the incident
 * detail page's embedded fix panel — renders these five states with the SAME
 * color + icon (`changeStatusMeta.ts` owns the color/label vocabulary) so a
 * reader learns it once.
 *
 * `unknown` (an ambiguous send) is deliberately tinted like a caution state
 * (amber, the same tone the app already uses for "stale"/"medium risk") rather
 * than neutral gray — an ambiguous outcome asks for attention, it isn't a
 * settled fact the way "reverted" is. `revert_unknown` (an ambiguous REVERT —
 * the rollback itself was never confirmed, and the backend permanently
 * refuses to retry it) shares that same caution tone but keeps its own label
 * and icon, so a reader can tell an uncertain apply from an uncertain revert
 * at a glance instead of both collapsing into one "Unknown" pill.
 */

const ICON: Record<ChangeStatus, React.ReactNode> = {
  applying: <Loader2 size={12} strokeWidth={2.5} />,
  applied: <Check size={12} strokeWidth={2.5} />,
  failed: <X size={12} strokeWidth={2.5} />,
  unknown: <CircleHelp size={12} strokeWidth={2.5} />,
  reverted: <RotateCcw size={12} strokeWidth={2.5} />,
  revert_unknown: <CircleHelp size={12} strokeWidth={2.5} />,
};

export function ChangeStatusPill({
  status,
  className,
  title,
}: {
  status: string;
  className?: string;
  title?: string;
}) {
  const key = normalizeChangeStatus(status);
  const meta = CHANGE_STATUS_META[key];
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 h-[20px] px-1.5 rounded-full text-[12px] font-medium whitespace-nowrap',
        className,
      )}
      style={{ background: meta.fill, color: meta.color }}
      title={title}
    >
      {ICON[key]}
      {meta.label}
    </span>
  );
}
