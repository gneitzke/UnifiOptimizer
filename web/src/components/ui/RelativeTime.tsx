import { useEffect, useState } from 'react';

/**
 * Honest staleness labeling (docs §Interaction: "stale data labeled 'as of
 * HH:MM:SS', never presented as live"). Timestamps are epoch seconds (UTC in the
 * store); display is local time (global rule: store UTC, show local).
 *
 * - mode="as-of"    → "as of 14:23:07" — a fixed capture time, does not tick.
 * - mode="relative" → "3m ago" — refreshes on an interval.
 * - mode="at"       → "14:23" (or "Jul 23, 14:23" when not today) — an absolute
 *                     wall-clock time, used for future targets like a snooze
 *                     deadline where "3m ago" / "as of" would read as the past.
 */

type Mode = 'as-of' | 'relative' | 'at';

interface Props {
  /** Epoch seconds. */
  ts: number | null | undefined;
  mode?: Mode;
  /** Text when ts is missing. */
  fallback?: string;
  className?: string;
  prefix?: string;
}

function two(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

function clockLocal(ts: number): string {
  const d = new Date(ts * 1000);
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

function relative(ts: number, nowMs: number): string {
  const s = Math.max(0, Math.round(nowMs / 1000 - ts));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  return `${days}d ago`;
}

function iso(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

/** Absolute wall-clock: "HH:MM" today, else "Mon D, HH:MM". */
function atLocal(ts: number): string {
  const d = new Date(ts * 1000);
  const now = new Date();
  const hm = `${two(d.getHours())}:${two(d.getMinutes())}`;
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return hm;
  return `${d.toLocaleString(undefined, { month: 'short' })} ${d.getDate()}, ${hm}`;
}

export function RelativeTime({
  ts,
  mode = 'as-of',
  fallback = 'unknown',
  className,
  prefix,
}: Props) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (mode !== 'relative') return;
    const t = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(t);
  }, [mode]);

  if (ts == null || !Number.isFinite(ts)) {
    return <span className={className}>{fallback}</span>;
  }

  const text =
    mode === 'as-of'
      ? `as of ${clockLocal(ts)}`
      : mode === 'at'
        ? atLocal(ts)
        : relative(ts, now);

  return (
    <time
      dateTime={new Date(ts * 1000).toISOString()}
      title={iso(ts)}
      className={className}
    >
      {prefix}
      {text}
    </time>
  );
}
