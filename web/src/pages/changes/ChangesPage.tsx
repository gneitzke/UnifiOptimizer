import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ChevronDown, ChevronRight, Layers } from 'lucide-react';
import { Button, Card, EmptyState, Skeleton, exactLocal } from '../../components/ui';
import { listChanges, useAsync, type ChangeRecord } from '../../api';
import { useListNavigation } from '../../layout/keyboard/useListNavigation';
import { ChangeStatusPill } from '../shared/changeStatus';
import { changeStatusUrgency, normalizeChangeStatus } from '../shared/changeStatusMeta';
import { useNowSeconds } from '../shared/hooks';
import { DiffView } from './DiffView';

/**
 * /changes — the config-change ledger. Each row expands inline (comparison-
 * oriented, short, viewed across many rows — DESIGN_FOUNDATION §Interaction) to
 * a mono before/after diff. Revert itself happens from the issue detail page
 * (ProposedFix), which is the only place a current diff can be reviewed
 * against the issue's live fix state before it sends — this ledger stays
 * read-only and links there instead of pretending a bare "Revert" button here
 * could safely fire the write (audit U4).
 *
 * `?id=<change id>` (the issue detail page's lifecycle trail links here —
 * Gitea #18 item 4) auto-expands and scrolls to that one row, marked with an
 * accent rail so landing here from a link is unambiguous.
 *
 * Time column uses the shared `exactLocal` stamp (24-hour, dated once it's not
 * today) — the same clock the health card and the timeline use, not a
 * locale-dependent (and previously AM/PM-leaking) format of its own (Gitea #25).
 *
 * Audit U4 additions: device / status / time filters; multi-step fix attempts
 * ledgered as separate rows now group under one header (a joint band re-plan
 * writes one change per radio moved, and reading those as unrelated singleton
 * rows hid that they were one attempt); and failed/uncertain attempts sort to
 * the top, because those are the ones asking for a decision.
 */

const GROUP_WINDOW_S = 5;

function humanizeAction(action: string): string {
  return action
    .replace(/[._]/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/^\w/, (c) => c.toUpperCase());
}

/** One multi-step fix attempt's changes, grouped together (audit U4). Prefers
 * an explicit `batch_id` from the backend; until that ships, falls back to a
 * same-issue-within-a-few-seconds heuristic — good enough to keep a joint
 * band re-plan's rows together without claiming precision the ledger doesn't
 * actually have yet. */
function groupKey(c: ChangeRecord): string {
  if (c.batch_id != null) return `b:${c.batch_id}`;
  return `h:${c.issue_id ?? 'none'}:${Math.floor(c.ts / GROUP_WINDOW_S)}`;
}

interface ChangeGroup {
  key: string;
  items: ChangeRecord[];
  worstStatus: string;
  latestTs: number;
}

function groupChanges(changes: ChangeRecord[]): ChangeGroup[] {
  const byKey = new Map<string, ChangeRecord[]>();
  for (const c of changes) {
    const k = groupKey(c);
    const list = byKey.get(k) ?? [];
    list.push(c);
    byKey.set(k, list);
  }
  const groups: ChangeGroup[] = [];
  for (const [key, items] of byKey) {
    items.sort((a, b) => a.ts - b.ts);
    const worstStatus = items.reduce(
      (worst, c) => (changeStatusUrgency(c.status) > changeStatusUrgency(worst) ? c.status : worst),
      items[0].status,
    );
    groups.push({ key, items, worstStatus, latestTs: Math.max(...items.map((c) => c.ts)) });
  }
  // Failed/uncertain attempts first (audit U4), newest-first within each tier.
  groups.sort((a, b) => {
    const urgencyDiff = changeStatusUrgency(b.worstStatus) - changeStatusUrgency(a.worstStatus);
    if (urgencyDiff !== 0) return urgencyDiff;
    return b.latestTs - a.latestTs;
  });
  return groups;
}

type TimeFilter = 'all' | '1h' | '24h' | '7d';
const TIME_OPTIONS: { value: TimeFilter; label: string; seconds: number | null }[] = [
  { value: 'all', label: 'All time', seconds: null },
  { value: '1h', label: 'Last hour', seconds: 3600 },
  { value: '24h', label: 'Last 24h', seconds: 86400 },
  { value: '7d', label: 'Last 7 days', seconds: 7 * 86400 },
];

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'All statuses' },
  { value: 'failed', label: 'Failed' },
  { value: 'unknown', label: 'Unknown' },
  { value: 'applying', label: 'Applying' },
  { value: 'applied', label: 'Applied' },
  { value: 'reverted', label: 'Reverted' },
];

function deviceKey(c: ChangeRecord): string {
  return c.entity ? String(c.entity.entity_id) : 'none';
}

function deviceLabel(c: ChangeRecord): string {
  return c.entity?.name ?? c.entity?.native_id ?? 'Unattributed';
}

function DiffAndRevert({ change }: { change: ChangeRecord }) {
  return (
    <div className="px-4 pb-4 pt-1" style={{ background: 'var(--canvas)' }}>
      <DiffView before={change.before} after={change.after} />
      <div className="flex items-center gap-3 mt-3">
        {change.issue_id != null ? (
          <Link
            to={`/issues/${change.issue_id}?revert_change=${change.id}`}
            className="t-caption hover:underline"
            style={{ color: 'var(--accent)' }}
          >
            Review revert on issue →
          </Link>
        ) : (
          <span
            className="t-caption"
            style={{ color: 'var(--fg-subtle)' }}
            title="This change has no associated issue, so there is no fix-history entry to revert from."
          >
            No issue to review a revert on
          </span>
        )}
        <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
          {change.status === 'reverted' && change.reverted_ts
            ? `Reverted ${exactLocal(change.reverted_ts)}.`
            : "This ledger is read-only. Revert is reviewed and confirmed from the issue's detail page."}
        </span>
      </div>
    </div>
  );
}

const ROW_COLS = '24px 180px 1fr 140px 110px';

function ChangeRow({
  change,
  open,
  onToggle,
  isLinked,
  isActive,
  registerRef,
  indent = false,
}: {
  change: ChangeRecord;
  open: boolean;
  onToggle: () => void;
  isLinked: boolean;
  isActive: boolean;
  registerRef?: (el: HTMLDivElement | null) => void;
  indent?: boolean;
}) {
  return (
    <div ref={registerRef} style={{ borderBottom: '1px solid var(--hairline)' }}>
      <div
        role="button"
        tabIndex={-1}
        aria-expanded={open}
        onClick={onToggle}
        className="grid items-center px-4 py-2.5 cursor-pointer transition-colors"
        style={{
          gridTemplateColumns: ROW_COLS,
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
        <span className="tnum t-secondary" style={{ color: 'var(--fg-muted)', paddingLeft: indent ? 16 : 0 }}>
          {exactLocal(change.ts)}
        </span>
        <span style={{ color: 'var(--fg)' }} className="truncate">
          {humanizeAction(change.action)}
        </span>
        <span className="truncate" style={{ color: change.entity ? 'var(--fg)' : 'var(--fg-subtle)' }}>
          {change.entity?.name ?? '—'}
        </span>
        <span className="flex justify-end">
          <ChangeStatusPill status={change.status} />
        </span>
      </div>
      {open && <DiffAndRevert change={change} />}
    </div>
  );
}

function GroupHeaderRow({
  group,
  open,
  onToggle,
  isActive,
}: {
  group: ChangeGroup;
  open: boolean;
  onToggle: () => void;
  isActive: boolean;
}) {
  const devices = new Set(group.items.map(deviceKey));
  const deviceText =
    devices.size === 1 ? deviceLabel(group.items[0]) : `${devices.size} devices`;
  const failedOrUnknown = group.items.filter((c) => {
    const s = normalizeChangeStatus(c.status);
    return s === 'failed' || s === 'unknown';
  }).length;

  return (
    <div style={{ borderBottom: '1px solid var(--hairline)' }}>
      <div
        role="button"
        tabIndex={-1}
        aria-expanded={open}
        onClick={onToggle}
        className="grid items-center px-4 py-2.5 cursor-pointer transition-colors"
        style={{
          gridTemplateColumns: ROW_COLS,
          gap: 12,
          background: isActive ? 'var(--canvas)' : undefined,
        }}
      >
        <span style={{ color: 'var(--fg-subtle)' }} aria-hidden>
          {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </span>
        <span className="tnum t-secondary" style={{ color: 'var(--fg-muted)' }}>
          {exactLocal(group.items[0].ts)}
        </span>
        <span className="flex items-center gap-1.5 min-w-0" style={{ color: 'var(--fg)' }}>
          <Layers size={13} style={{ color: 'var(--fg-subtle)' }} aria-hidden />
          <span className="truncate">Fix attempt · {group.items.length} steps</span>
          {failedOrUnknown > 0 && (
            <span className="t-micro" style={{ color: 'var(--sev-p1)' }}>
              ({failedOrUnknown} needs review)
            </span>
          )}
        </span>
        <span className="truncate" style={{ color: devices.size === 1 ? 'var(--fg)' : 'var(--fg-subtle)' }}>
          {deviceText}
        </span>
        <span className="flex justify-end">
          <ChangeStatusPill status={group.worstStatus} />
        </span>
      </div>
    </div>
  );
}

export default function ChangesPage() {
  const { data, loading, error, reload } = useAsync(() => listChanges(), []);
  const [expanded, setExpanded] = useState<Set<number | string>>(() => new Set());
  const [searchParams, setSearchParams] = useSearchParams();
  const rawId = searchParams.get('id');
  const linkedId = rawId != null && /^\d+$/.test(rawId) ? Number(rawId) : null;
  const linkedRowRef = useRef<HTMLDivElement | null>(null);
  const scrolledToLinked = useRef(false);

  const deviceFilter = searchParams.get('device') ?? '';
  const statusFilter = searchParams.get('status') ?? '';
  const timeFilter = (searchParams.get('time') as TimeFilter) || 'all';

  function patch(next: Record<string, string | null>) {
    const params = new URLSearchParams(searchParams);
    for (const [k, v] of Object.entries(next)) {
      if (v == null || v === '') params.delete(k);
      else params.set(k, v);
    }
    setSearchParams(params, { replace: true });
  }

  const allChanges = useMemo(() => data?.changes ?? [], [data]);

  const devices = useMemo(() => {
    const map = new Map<string, string>();
    for (const c of allChanges) {
      if (c.entity) map.set(deviceKey(c), deviceLabel(c));
    }
    return Array.from(map, ([value, label]) => ({ value, label })).sort((a, b) =>
      a.label.localeCompare(b.label),
    );
  }, [allChanges]);

  const now = useNowSeconds();
  const changes = useMemo(() => {
    const seconds = TIME_OPTIONS.find((o) => o.value === timeFilter)?.seconds ?? null;
    const cutoff = seconds != null ? now - seconds : null;
    return allChanges.filter((c) => {
      if (deviceFilter && deviceKey(c) !== deviceFilter) return false;
      if (statusFilter && normalizeChangeStatus(c.status) !== statusFilter) return false;
      if (cutoff != null && c.ts < cutoff) return false;
      return true;
    });
  }, [allChanges, deviceFilter, statusFilter, timeFilter, now]);

  const groups = useMemo(() => groupChanges(changes), [changes]);
  const hasActiveFilters = !!deviceFilter || !!statusFilter || timeFilter !== 'all';

  // Deep link from a lifecycle-trail "change #N": expand that row (and its
  // group, if it's part of one) and bring it into view once.
  useEffect(() => {
    if (linkedId == null || scrolledToLinked.current) return;
    const group = groups.find((g) => g.items.some((c) => c.id === linkedId));
    if (!group) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      next.add(linkedId);
      if (group.items.length > 1) next.add(group.key);
      return next;
    });
    linkedRowRef.current?.scrollIntoView({ block: 'center' });
    scrolledToLinked.current = true;
  }, [linkedId, groups]);

  function toggle(key: number | string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const nav = useListNavigation(groups.length, (i) => {
    const g = groups[i];
    if (g) toggle(g.items.length > 1 ? g.key : g.items[0].id);
  });

  return (
    <div className="px-6 sm:px-8 py-8 max-w-[1100px] mx-auto">
      <h2 className="t-page-title mb-1" style={{ color: 'var(--fg)' }}>
        Changes
      </h2>
      <p className="t-secondary mb-5" style={{ color: 'var(--fg-muted)' }}>
        Every config change the fix engine applies, with the exact before/after.
        Failed and uncertain attempts sort to the top. Revert is reviewed and
        confirmed from the issue it belongs to.
      </p>

      {!loading && !error && allChanges.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <select
            value={deviceFilter}
            onChange={(e) => patch({ device: e.target.value || null })}
            className="h-8 px-2 rounded-control t-body cursor-pointer outline-none"
            style={{ background: 'var(--surface)', border: '1px solid var(--strong)', color: 'var(--fg)' }}
            aria-label="Filter by device"
          >
            <option value="">All devices</option>
            {devices.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>

          <select
            value={statusFilter}
            onChange={(e) => patch({ status: e.target.value || null })}
            className="h-8 px-2 rounded-control t-body cursor-pointer outline-none"
            style={{ background: 'var(--surface)', border: '1px solid var(--strong)', color: 'var(--fg)' }}
            aria-label="Filter by status"
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>

          <div
            className="inline-flex rounded-control overflow-hidden"
            style={{ border: '1px solid var(--strong)' }}
            role="group"
            aria-label="Filter by time"
          >
            {TIME_OPTIONS.map((o) => {
              const active = timeFilter === o.value;
              return (
                <button
                  key={o.value}
                  type="button"
                  aria-pressed={active}
                  onClick={() => patch({ time: o.value === 'all' ? null : o.value })}
                  className="h-8 px-2.5 t-caption cursor-pointer transition-colors whitespace-nowrap"
                  style={{
                    background: active ? 'var(--accent)' : 'transparent',
                    color: active ? 'var(--accent-fg)' : 'var(--fg-muted)',
                  }}
                >
                  {o.label}
                </button>
              );
            })}
          </div>

          {hasActiveFilters && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => patch({ device: null, status: null, time: null })}
            >
              Clear filters
            </Button>
          )}

          <span className="t-secondary tnum ml-auto" style={{ color: 'var(--fg-subtle)' }}>
            {changes.length} shown of {allChanges.length}
          </span>
        </div>
      )}

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
      ) : allChanges.length === 0 ? (
        <Card>
          <EmptyState
            variant="no-data"
            title="No changes recorded"
            description="Nothing has been applied to the network yet. Applied fixes will appear here with a full before/after and revert."
          />
        </Card>
      ) : groups.length === 0 ? (
        <Card>
          <EmptyState
            variant="no-match"
            title="No changes match these filters"
            description="Try a different device, status, or time range."
            action={{ label: 'Clear filters', onClick: () => patch({ device: null, status: null, time: null }) }}
          />
        </Card>
      ) : (
        <Card pad="none">
          {/* header row */}
          <div
            className="grid items-center px-4 h-9 t-label"
            style={{
              gridTemplateColumns: ROW_COLS,
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
            {groups.map((group, i) => {
              const rp = nav.getRowProps(i);
              const isActive = i === nav.activeIndex;
              const isMulti = group.items.length > 1;

              if (!isMulti) {
                const c = group.items[0];
                const isLinked = c.id === linkedId;
                return (
                  <ChangeRow
                    key={group.key}
                    change={c}
                    open={expanded.has(c.id)}
                    onToggle={() => {
                      rp.onMouseEnter();
                      toggle(c.id);
                    }}
                    isLinked={isLinked}
                    isActive={isActive}
                    registerRef={(el) => {
                      (rp.ref as (el: HTMLDivElement | null) => void)(el);
                      if (isLinked) linkedRowRef.current = el;
                    }}
                  />
                );
              }

              const groupOpen = expanded.has(group.key);
              return (
                <div
                  key={group.key}
                  ref={(el: HTMLDivElement | null) => {
                    (rp.ref as (el: HTMLDivElement | null) => void)(el);
                    if (group.items.some((c) => c.id === linkedId)) linkedRowRef.current = el;
                  }}
                  onMouseEnter={rp.onMouseEnter}
                >
                  <GroupHeaderRow
                    group={group}
                    open={groupOpen}
                    onToggle={() => toggle(group.key)}
                    isActive={isActive}
                  />
                  {groupOpen &&
                    group.items.map((c) => (
                      <ChangeRow
                        key={c.id}
                        change={c}
                        open={expanded.has(c.id)}
                        onToggle={() => toggle(c.id)}
                        isLinked={c.id === linkedId}
                        isActive={false}
                        indent
                      />
                    ))}
                </div>
              );
            })}
          </div>
        </Card>
      )}
    </div>
  );
}
