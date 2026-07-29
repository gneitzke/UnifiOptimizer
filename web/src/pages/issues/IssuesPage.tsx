import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Search } from 'lucide-react';
import { EmptyState } from '../../components/ui/EmptyState';
import { Skeleton } from '../../components/ui/Skeleton';
import { useRegisterFilter } from '../../layout/keyboard/filterFocusContext';
import { useWsFrames } from '../../api/WsProvider';
import { isSuppressedNow, issueDurationSeconds, severityRank } from '../shared/format';
import { entityLabel, listIssues, type IssueRow } from '../shared/api';
import { usePageAsync, useNowSeconds } from '../shared/hooks';
import { IssueRowsList, type DisplayRow, type IssueGroup } from './IssueRowsList';
import type { Severity } from '../../api/types';

/**
 * Issues list (`/issues`) — the product's heart, and (Gitea #21) the only
 * place open work lives: there is no separate Incidents nav destination. A
 * genuine incident (2+ correlated issues) folds its members into one group
 * row, expandable inline; a standalone issue renders exactly as it always
 * has. Filters live in the URL so a dashboard link like
 * `?severity=p1&state=active` lands pre-filtered. `/` focuses the text
 * filter; j/k + Enter traverse and open rows.
 */

/**
 * The state filter, read off the lifecycle itself (§7: pending -> active ->
 * resolving -> resolved). "Open" and "Active" are not the same set and never
 * were — Open is everything not resolved, Active is only the confirmed,
 * still-firing middle of that — but two bare labels, one of which is a subset of
 * the other, gave a reader no way to know that (Gitea #24). So the subset says
 * it is one, and every option carries the states it actually matches.
 */
type StateFilter = 'open' | 'active' | 'suppressed' | 'resolved' | 'all';
const STATE_OPTIONS: { value: StateFilter; label: string; hint: string }[] = [
  {
    value: 'open',
    label: 'Open',
    hint: 'Everything not resolved and not suppressed: the issues asking for attention.',
  },
  {
    value: 'active',
    label: 'Active only',
    hint: 'The confirmed, still-firing subset of Open (suppressed issues excluded). Excludes pending (not yet confirmed) and resolving (clearing).',
  },
  {
    value: 'suppressed',
    label: 'Suppressed',
    hint: 'Open issues an operator has muted (Gitea #49): excluded from counts and alerts, still measured. Their facts are unchanged.',
  },
  {
    value: 'resolved',
    label: 'Resolved',
    hint: 'Closed after the detector reported the condition clear for enough consecutive cycles.',
  },
  { value: 'all', label: 'All', hint: 'Every issue, resolved history and suppressed included.' },
];
const SEV_OPTIONS: { value: '' | Severity; label: string }[] = [
  { value: '', label: 'All severities' },
  { value: 'p1', label: 'Critical' },
  { value: 'p2', label: 'High' },
  { value: 'p3', label: 'Low' },
];

function stateMatches(state: string, filter: StateFilter): boolean {
  if (filter === 'all') return true;
  if (filter === 'suppressed') return state !== 'resolved'; // suppression tested separately
  if (filter === 'open') return state !== 'resolved';
  return state === filter;
}

/** Whether a display row is suppressed *now*. An incident group is suppressed
 * only when ALL its members are (Gitea #49): a suppressed root with a live
 * symptom keeps its claim on attention, because the symptom is unanswered. */
function rowSuppressed(r: DisplayRow, now: number): boolean {
  if (r.kind === 'issue') return isSuppressedNow(r.issue, now);
  return (
    isSuppressedNow(r.group.root, now) &&
    r.group.symptoms.every((s) => isSuppressedNow(s, now))
  );
}

/** Group issues that share a genuine `incident_brief` into `IssueGroup`s, and
 * return the remaining (ungrouped) issues separately. Purely a client-side
 * fold over one `/api/issues` response — no second fetch, since root +
 * symptoms are all already in `issues`. */
function groupIssues(issues: IssueRow[]): { groups: IssueGroup[]; solo: IssueRow[] } {
  const byIncident = new Map<number, IssueRow[]>();
  for (const issue of issues) {
    const brief = issue.incident_brief;
    if (!brief) continue;
    const members = byIncident.get(brief.id) ?? [];
    members.push(issue);
    byIncident.set(brief.id, members);
  }

  const groups: IssueGroup[] = [];
  const groupedIds = new Set<number>();
  for (const [incidentId, members] of byIncident) {
    const root = members.find((m) => m.incident_role === 'root');
    // Defensive: a brief always carries its own root as a member in the same
    // response. Skip silently rather than fabricate one if it somehow didn't.
    if (!root) continue;
    for (const m of members) groupedIds.add(m.id);
    groups.push({
      incidentId,
      title: root.incident_brief!.title,
      summary: root.incident_brief!.summary,
      severity: root.incident_brief!.severity,
      root,
      symptoms: members.filter((m) => m.id !== root.id),
    });
  }

  const solo = issues.filter((i) => !groupedIds.has(i.id));
  return { groups, solo };
}

export function IssuesPage() {
  const registerFilter = useRegisterFilter();
  const [params, setParams] = useSearchParams();

  const stateFilter = (params.get('state') as StateFilter) || 'open';
  const sevFilter = (params.get('severity') as Severity | null) ?? '';
  const query = params.get('q') ?? '';

  const { data, loading, error, reload } = usePageAsync(() => listIssues(), [], {
    pollMs: 30_000,
  });
  useWsFrames((frame) => {
    if (frame.type === 'issue_transition') reload();
  });

  const now = useNowSeconds();

  const patch = (next: Record<string, string | null>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v === null || v === '') p.delete(k);
      else p.set(k, v);
    }
    setParams(p, { replace: true });
  };

  const { groups, solo } = useMemo(() => groupIssues(data?.issues ?? []), [data]);

  const displayRows = useMemo<DisplayRow[]>(() => {
    const groupRows: DisplayRow[] = groups.map((group) => ({ kind: 'group', group }));
    const soloRows: DisplayRow[] = solo.map((issue) => ({ kind: 'issue', issue }));
    return [...groupRows, ...soloRows];
  }, [groups, solo]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = displayRows.filter((r) => {
      const state = r.kind === 'group' ? r.group.root.state : r.issue.state;
      if (!stateMatches(state, stateFilter)) return false;
      // Suppression is an orthogonal axis on the open states, not a state itself
      // (Gitea #49). The attention views (Open, Active) hide suppressed rows; the
      // Suppressed view shows only them; All and Resolved are unaffected.
      const supp = rowSuppressed(r, now);
      if (stateFilter === 'suppressed' && !supp) return false;
      if ((stateFilter === 'open' || stateFilter === 'active') && supp) return false;
      const severity = r.kind === 'group' ? r.group.severity : r.issue.severity;
      if (sevFilter && severity !== sevFilter) return false;
      if (q) {
        const hay =
          r.kind === 'group'
            ? [
                r.group.title,
                r.group.summary,
                r.group.root.detector_key,
                entityLabel(r.group.root.entity),
                ...r.group.symptoms.map((s) => s.title),
              ]
                .join(' ')
                .toLowerCase()
            : `${r.issue.title} ${r.issue.detector_key} ${entityLabel(r.issue.entity)}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    // Default order: most severe first, then longest-running (a group's age is
    // its root's — the incident's identity is the root's fingerprint).
    return rows.sort((a, b) => {
      const sevA = a.kind === 'group' ? a.group.severity : a.issue.severity;
      const sevB = b.kind === 'group' ? b.group.severity : b.issue.severity;
      const s = severityRank(sevA) - severityRank(sevB);
      if (s !== 0) return s;
      const durA = a.kind === 'group' ? now - a.group.root.first_seen_ts : issueDurationSeconds(a.issue, now);
      const durB = b.kind === 'group' ? now - b.group.root.first_seen_ts : issueDurationSeconds(b.issue, now);
      return durB - durA;
    });
  }, [displayRows, stateFilter, sevFilter, query, now]);

  const total = data?.issues.length ?? 0;
  const hasActiveFilters = !!sevFilter || !!query.trim() || stateFilter !== 'open';

  // The reconciliation line (Gitea #21): one honest number in the nav badge,
  // and here the sentence that explains it — "14 open issues · 1 incident
  // groups 4 of them" — instead of two nav destinations quietly disagreeing.
  // Suppression (Gitea #49) shrinks the open count; the shrink is disclosed here,
  // never silent — "6 open issues · 3 suppressed" so the Open list showing 6 rows
  // reconciles with the 3 the operator has muted.
  const openRows = (data?.issues ?? []).filter((i) => i.state !== 'resolved');
  const suppressedOpenCount = openRows.filter((i) => isSuppressedNow(i, now)).length;
  const openIssueCount = openRows.length - suppressedOpenCount; // the attention count
  const openGroups = groups.filter((g) => g.root.state !== 'resolved');
  const groupedOpenIssueCount = openGroups.reduce((n, g) => n + 1 + g.symptoms.length, 0);
  // Once a filter or search narrows the list, the standing reconciliation is no
  // longer what the reader is looking at, so report the result of their query
  // instead. A "No matches" state under a header still claiming 14 open issues is
  // the page contradicting itself.
  const filtering = query.trim() !== '' || filtered.length !== total;
  const openLabel = `${openIssueCount} open issue${openIssueCount === 1 ? '' : 's'}`;
  const suppressedClause = suppressedOpenCount > 0 ? ` · ${suppressedOpenCount} suppressed` : '';
  const incidentClause =
    openGroups.length === 0
      ? ''
      : ` · ${openGroups.length} incident${openGroups.length === 1 ? '' : 's'} ` +
        `group${openGroups.length === 1 ? 's' : ''} ${groupedOpenIssueCount} of them`;
  const reconciliation = `${openLabel}${suppressedClause}${incidentClause}`;

  function renderEmpty() {
    if (total === 0) {
      return (
        <EmptyState
          variant="no-data"
          title="No issues recorded yet"
          description="The daemon opens an issue when a detector's condition holds. Nothing has crossed a threshold yet."
        />
      );
    }
    if (hasActiveFilters) {
      return (
        <EmptyState
          variant="no-match"
          description="No issues match the current filters."
          action={{
            label: 'Clear filters',
            onClick: () => setParams(new URLSearchParams(), { replace: true }),
          }}
        />
      );
    }
    // Default view (open) with nothing open, but resolved history exists.
    return <EmptyState variant="healthy" title="No open issues" />;
  }

  return (
    <div className="px-6 py-6 mx-auto flex flex-col gap-4" style={{ maxWidth: 1200 }}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <h2 className="t-page-title" style={{ color: 'var(--fg)' }}>
            Issues
          </h2>
          {data && (
            <span className="t-secondary tnum" style={{ color: 'var(--fg-muted)' }}>
              {filtering ? `${filtered.length} shown of ${total}` : reconciliation}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="relative flex items-center">
            <Search
              size={14}
              className="absolute left-2.5 pointer-events-none"
              style={{ color: 'var(--fg-subtle)' }}
            />
            <input
              ref={registerFilter}
              type="text"
              value={query}
              placeholder="Filter…  ( / )"
              onChange={(e) => patch({ q: e.target.value })}
              className="h-8 w-52 pl-8 pr-2 rounded-control t-body outline-none"
              style={{
                background: 'var(--surface)',
                border: '1px solid var(--strong)',
                color: 'var(--fg)',
              }}
            />
          </label>

          <select
            value={sevFilter}
            onChange={(e) => patch({ severity: e.target.value || null })}
            className="h-8 px-2 rounded-control t-body cursor-pointer outline-none"
            style={{ background: 'var(--surface)', border: '1px solid var(--strong)', color: 'var(--fg)' }}
            aria-label="Filter by severity"
          >
            {SEV_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>

          <div
            className="inline-flex rounded-control overflow-hidden"
            style={{ border: '1px solid var(--strong)' }}
            role="group"
            aria-label="Filter by state"
          >
            {STATE_OPTIONS.map((o) => {
              const active = stateFilter === o.value;
              return (
                <button
                  key={o.value}
                  type="button"
                  aria-pressed={active}
                  title={o.hint}
                  onClick={() => patch({ state: o.value === 'open' ? null : o.value })}
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
        </div>
      </div>

      {error ? (
        <EmptyState
          variant="no-data"
          title="Could not load issues"
          description="The daemon may still be starting, or the API is unreachable."
        />
      ) : loading && !data ? (
        <div className="flex flex-col gap-2 pt-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-11 w-full" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        renderEmpty()
      ) : (
        <IssueRowsList rows={filtered} now={now} />
      )}

      {/* Suppressed-but-open rows do not vanish silently: in the Open view, a
          footer discloses how many are muted and links to the Suppressed segment
          (Gitea #49). They are still active and still measured. */}
      {stateFilter === 'open' && suppressedOpenCount > 0 && (
        <button
          type="button"
          onClick={() => patch({ state: 'suppressed' })}
          className="self-start t-caption hover:underline cursor-pointer"
          style={{ color: 'var(--fg-muted)' }}
        >
          {suppressedOpenCount} suppressed issue{suppressedOpenCount === 1 ? '' : 's'}{' '}
          {suppressedOpenCount === 1 ? 'is' : 'are'} still active ·{' '}
          <span style={{ color: 'var(--accent)' }}>view</span>
        </button>
      )}
    </div>
  );
}
