import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Check, ChevronDown, ChevronRight, TriangleAlert } from 'lucide-react';
import { Button, Card, EmptyState, Skeleton, exactLocal } from '../../components/ui';
import { listChanges, useAsync, type ChangeRecord } from '../../api';
import { useListNavigation } from '../../layout/keyboard/useListNavigation';
import { DiffView } from './DiffView';

/**
 * /changes — the config-change ledger. Each row expands inline (comparison-
 * oriented, short, viewed across many rows — DESIGN_FOUNDATION §Interaction) to
 * a mono before/after diff. Revert itself works, but from the issue detail
 * page (ProposedFix), not from this ledger — this row's control stays
 * disabled with a note pointing there. Read-only here, keyboard-traversable
 * (j/k/arrows, Enter expands).
 *
 * `?id=<change id>` (the issue detail page's lifecycle trail links here —
 * Gitea #18 item 4) auto-expands and scrolls to that one row, marked with an
 * accent rail so landing here from a link is unambiguous.
 *
 * Time column uses the shared `exactLocal` stamp (24-hour, dated once it's not
 * today) — the same clock the health card and the timeline use, not a
 * locale-dependent (and previously AM/PM-leaking) format of its own (Gitea #25).
 */

function StatusChip({ status }: { status: ChangeRecord['status'] }) {
  const base =
    'inline-flex items-center gap-1 h-[20px] px-1.5 rounded-full text-[12px] font-medium whitespace-nowrap';
  if (status === 'failed') {
    return (
      <span
        className={base}
        style={{ background: 'var(--sev-p1-fill)', color: 'var(--sev-p1)' }}
      >
        <TriangleAlert size={12} strokeWidth={2.5} />
        Failed
      </span>
    );
  }
  if (status === 'reverted') {
    return (
      <span
        className={base}
        style={{ background: 'var(--sev-neutral-fill)', color: 'var(--sev-neutral)' }}
      >
        <Check size={12} strokeWidth={2.5} />
        Reverted
      </span>
    );
  }
  return (
    <span
      className={base}
      style={{ background: 'color-mix(in srgb, var(--accent) 12%, transparent)', color: 'var(--accent)' }}
    >
      Applied
    </span>
  );
}

function humanizeAction(action: string): string {
  return action
    .replace(/[._]/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/^\w/, (c) => c.toUpperCase());
}

export default function ChangesPage() {
  const { data, loading, error, reload } = useAsync(() => listChanges(), []);
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set());
  const [searchParams] = useSearchParams();
  const rawId = searchParams.get('id');
  const linkedId = rawId != null && /^\d+$/.test(rawId) ? Number(rawId) : null;
  const linkedRowRef = useRef<HTMLDivElement | null>(null);
  const scrolledToLinked = useRef(false);

  const changes = data?.changes ?? [];

  // Deep link from a lifecycle-trail "change #N": expand that row and bring it
  // into view once, the first time it's present in a loaded page of changes.
  useEffect(() => {
    if (linkedId == null || scrolledToLinked.current) return;
    if (!changes.some((c) => c.id === linkedId)) return;
    setExpanded((prev) => (prev.has(linkedId) ? prev : new Set(prev).add(linkedId)));
    linkedRowRef.current?.scrollIntoView({ block: 'center' });
    scrolledToLinked.current = true;
  }, [linkedId, changes]);

  function toggle(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const nav = useListNavigation(changes.length, (i) => {
    const row = changes[i];
    if (row) toggle(row.id);
  });

  return (
    <div className="px-6 sm:px-8 py-8 max-w-[1100px] mx-auto">
      <h2 className="t-page-title mb-1" style={{ color: 'var(--fg)' }}>
        Changes
      </h2>
      <p className="t-secondary mb-5" style={{ color: 'var(--fg-muted)' }}>
        Every config change the fix engine applies, newest first, with the exact
        before/after. Revert a change from the issue it belongs to.
      </p>

      {loading ? (
        <Card>
          <Skeleton className="h-6 w-full mb-3" />
          <Skeleton className="h-6 w-full mb-3" />
          <Skeleton className="h-6 w-2/3" />
        </Card>
      ) : error ? (
        <Card>
          <EmptyState
            variant="no-data"
            title="Couldn't load the change ledger"
            description={
              error.status === 0
                ? 'The API is unreachable. Is the daemon running?'
                : `The changes endpoint returned ${error.status}.`
            }
            action={{ label: 'Retry', onClick: reload }}
          />
        </Card>
      ) : changes.length === 0 ? (
        <Card>
          <EmptyState
            variant="no-data"
            title="No changes recorded"
            description="Nothing has been applied to the network yet. Applied fixes will appear here with a full before/after and revert."
          />
        </Card>
      ) : (
        <Card pad="none">
          {/* header row */}
          <div
            className="grid items-center px-4 h-9 t-label"
            style={{
              gridTemplateColumns: '24px 180px 1fr 140px 110px',
              gap: 12,
              color: 'var(--fg-muted)',
              borderBottom: '1px solid var(--hairline)',
            }}
          >
            <span />
            <span>Time</span>
            <span>Action</span>
            <span>Entity</span>
            <span className="text-right">Status</span>
          </div>

          <div
            tabIndex={nav.containerProps.tabIndex}
            role={nav.containerProps.role}
            onKeyDown={nav.containerProps.onKeyDown}
            className="outline-none"
            aria-label="Change ledger"
          >
            {changes.map((c, i) => {
              const rp = nav.getRowProps(i);
              const isActive = i === nav.activeIndex;
              const isLinked = c.id === linkedId;
              const open = expanded.has(c.id);
              return (
                <div
                  key={c.id}
                  ref={(el: HTMLDivElement | null) => {
                    (rp.ref as (el: HTMLDivElement | null) => void)(el);
                    if (isLinked) linkedRowRef.current = el;
                  }}
                  aria-selected={rp['aria-selected']}
                  onMouseEnter={rp.onMouseEnter}
                  style={{ borderBottom: '1px solid var(--hairline)' }}
                >
                  <div
                    role="button"
                    tabIndex={-1}
                    aria-expanded={open}
                    onClick={() => toggle(c.id)}
                    className="grid items-center px-4 py-2.5 cursor-pointer transition-colors"
                    style={{
                      gridTemplateColumns: '24px 180px 1fr 140px 110px',
                      gap: 12,
                      background: isLinked
                        ? 'color-mix(in srgb, var(--accent) 10%, transparent)'
                        : isActive
                          ? 'var(--canvas)'
                          : undefined,
                    }}
                  >
                    <span style={{ color: 'var(--fg-subtle)' }} aria-hidden>
                      {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                    </span>
                    <span className="tnum t-secondary" style={{ color: 'var(--fg-muted)' }}>
                      {exactLocal(c.ts)}
                    </span>
                    <span style={{ color: 'var(--fg)' }} className="truncate">
                      {humanizeAction(c.action)}
                    </span>
                    <span className="truncate" style={{ color: c.entity ? 'var(--fg)' : 'var(--fg-subtle)' }}>
                      {c.entity?.name ?? '—'}
                    </span>
                    <span className="flex justify-end">
                      <StatusChip status={c.status} />
                    </span>
                  </div>

                  {open && (
                    <div className="px-4 pb-4 pt-1" style={{ background: 'var(--canvas)' }}>
                      <DiffView before={c.before} after={c.after} />
                      <div className="flex items-center gap-3 mt-3">
                        <Button
                          variant="secondary"
                          size="sm"
                          disabled
                          title="Revert from the issue's detail page, not this ledger"
                        >
                          Revert
                        </Button>
                        <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
                          {c.status === 'reverted' && c.reverted_ts
                            ? `Reverted ${exactLocal(c.reverted_ts)}.`
                            : "This ledger is read-only. Revert from the issue's detail page."}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}
    </div>
  );
}
