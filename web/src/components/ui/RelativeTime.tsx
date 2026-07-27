import { useEffect, useState } from 'react';

/**
 * Honest staleness labeling (docs §Interaction: "stale data labeled 'as of
 * HH:MM:SS', never presented as live"). Timestamps are epoch seconds (UTC in the
 * store); display is local time (global rule: store UTC, show local).
 *
 * - mode="as-of"    → "as of 14:23:07" (or "as of Jul 23, 14:23:07" when not
 *                     today) — a fixed capture time, does not tick. Stale data
 *                     can be more than a day old, so the date is never dropped
 *                     silently (Gitea #25).
 * - mode="relative" → "3m ago" — refreshes on an interval.
 * - mode="at"       → "14:23" (or "Jul 23, 14:23" when not today) — an absolute
 *                     wall-clock time, used for future targets like a snooze
 *                     deadline where "3m ago" / "as of" would read as the past.
 * - mode="exact"    → "14:23:07" (or "Jul 23, 14:23:07" when not today) — a
 *                     definitive historical instant, e.g. a lifecycle-trail
 *                     entry: the date matters there because an issue can span
 *                     midnight, unlike "at"'s always-recent future target.
 *
 * Every mode is 24-hour (never locale AM/PM) and, wherever a value can be more
 * than a day old, includes the date once it stops being today — one formatting
 * rule for the whole app instead of each page re-implementing its own clock.
 */

type Mode = 'as-of' | 'relative' | 'at' | 'exact';

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

function sameLocalDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  );
}

/** Absolute wall-clock: "HH:MM" today, else "Mon D, HH:MM". */
function atLocal(ts: number): string {
  const d = new Date(ts * 1000);
  const hm = `${two(d.getHours())}:${two(d.getMinutes())}`;
  if (sameLocalDay(d, new Date())) return hm;
  return `${d.toLocaleString(undefined, { month: 'short' })} ${d.getDate()}, ${hm}`;
}

/** Exact wall-clock with seconds: "HH:MM:SS" today, else "Mon D, HH:MM:SS" —
 * a past instant that keeps its date once it's not today (an issue can span
 * midnight, so the lifecycle trail must not silently drop it). Shared by
 * mode="exact" and mode="as-of", and reusable wherever else the app needs the
 * same "24-hour, dated once stale" stamp (e.g. the timeline). */
export function exactLocal(ts: number): string {
  const d = new Date(ts * 1000);
  if (sameLocalDay(d, new Date())) return clockLocal(ts);
  return `${d.toLocaleString(undefined, { month: 'short' })} ${d.getDate()}, ${clockLocal(ts)}`;
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
      ? `as of ${exactLocal(ts)}`
      : mode === 'at'
        ? atLocal(ts)
        : mode === 'exact'
          ? exactLocal(ts)
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
