import type { ReactNode } from 'react';
import { Search } from 'lucide-react';
import { cn, SeverityGlyph } from '../../components/ui';
import { useRegisterFilter } from '../../layout/keyboard';
import type { IssueCounts, Severity } from './api';

/* ---- Filter input (claims the `/` hotkey) ------------------------------- */

export function FilterInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  const register = useRegisterFilter();
  return (
    <div
      className="inline-flex items-center gap-2 h-9 px-3 rounded-control"
      style={{ background: 'var(--surface)', border: '1px solid var(--strong)' }}
    >
      <Search size={15} style={{ color: 'var(--fg-subtle)' }} aria-hidden />
      <input
        ref={register}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="bg-transparent outline-none t-body w-44 sm:w-56"
        style={{ color: 'var(--fg)' }}
      />
    </div>
  );
}

/* ---- Time-range toggle (24h / 7d) -------------------------------------- */

export const RANGE_24H = 86_400;
export const RANGE_7D = 604_800;

export function RangeToggle({
  value,
  onChange,
}: {
  value: number;
  onChange: (seconds: number) => void;
}) {
  const opts: Array<{ label: string; seconds: number }> = [
    { label: '24h', seconds: RANGE_24H },
    { label: '7d', seconds: RANGE_7D },
  ];
  return (
    <div
      role="tablist"
      aria-label="Chart time range"
      className="inline-flex rounded-control p-0.5"
      style={{ background: 'var(--canvas)', border: '1px solid var(--hairline)' }}
    >
      {opts.map((o) => {
        const active = value === o.seconds;
        return (
          <button
            key={o.seconds}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(o.seconds)}
            className="h-7 px-3 rounded-[6px] t-caption font-medium tnum transition-colors cursor-pointer"
            style={{
              background: active ? 'var(--surface)' : 'transparent',
              color: active ? 'var(--fg)' : 'var(--fg-muted)',
              boxShadow: active ? 'var(--shadow-card)' : undefined,
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/* ---- Compact severity-count badges (shape + count, never color alone) --- */

const SEV_ORDER: Severity[] = ['p1', 'p2', 'p3'];
const SEV_COLOR: Record<Severity, string> = {
  p1: 'var(--sev-p1)',
  p2: 'var(--sev-p2)',
  p3: 'var(--sev-p3)',
};

export function IssueCountBadges({
  counts,
  className,
}: {
  counts: IssueCounts;
  className?: string;
}) {
  if (!counts || counts.total === 0) {
    // Healthy is a positive; a single quiet dot keeps the column aligned.
    return (
      <span
        className={cn('inline-flex items-center gap-1 t-caption tnum', className)}
        style={{ color: 'var(--fg-subtle)' }}
        title="No open issues"
      >
        <span
          aria-hidden
          className="inline-block w-1.5 h-1.5 rounded-full"
          style={{ background: 'var(--sev-neutral)' }}
        />
        0
      </span>
    );
  }
  return (
    <span className={cn('inline-flex items-center gap-2', className)}>
      {SEV_ORDER.map((sev) => {
        const n = counts[sev];
        if (!n) return null;
        return (
          <span
            key={sev}
            className="inline-flex items-center gap-1 t-caption tnum"
            style={{ color: SEV_COLOR[sev] }}
            title={`${n} open ${sev.toUpperCase()}`}
          >
            <SeverityGlyph severity={sev} size={10} />
            {n}
          </span>
        );
      })}
    </span>
  );
}

/* ---- Simple key/value row for a meta panel ------------------------------ */

export function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="t-secondary shrink-0" style={{ color: 'var(--fg-muted)' }}>
        {label}
      </span>
      <span className="t-body text-right tnum" style={{ color: 'var(--fg)' }}>
        {children}
      </span>
    </div>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h3 className="t-section mb-3" style={{ color: 'var(--fg)' }}>
      {children}
    </h3>
  );
}
