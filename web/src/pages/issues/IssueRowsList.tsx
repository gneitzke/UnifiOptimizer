import { useState, type RefCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { SeverityPill, SeverityGlyph } from '../../components/ui/SeverityPill';
import { StatePill } from '../../components/ui/StatePill';
import { RelativeTime } from '../../components/ui/RelativeTime';
import { useListNavigation } from '../../layout/keyboard/useListNavigation';
import { EntityLink } from '../shared/EntityLink';
import type { IssueRow } from '../shared/api';
import {
  ISSUE_IMPACT_DEFINITION,
  formatDuration,
  impactDisplay,
  ongoingLabel,
} from '../shared/format';
import type { Severity } from '../../api/types';

/**
 * The Issues list body. A solo issue renders one row, same columns as before. A
 * genuine incident (2+ members, Gitea #21) renders as ONE group row instead of
 * N scattered rows: the engine's title, its correlation summary as a second
 * line, severity = the incident's, and a "N issues" expander that reveals the
 * root (labeled) and its symptoms (indented) inline — every member is already
 * in this page's own issue list, so expanding needs no second fetch. Clicking
 * the row opens the incident's full story (`/incidents/:id`); the expander
 * button only toggles, it does not navigate.
 *
 * Column-header click-to-sort is intentionally not carried over from the old
 * DataTable: a genuine group and a solo issue do not share one natural sort
 * key past severity + duration (the default, and the only order this list
 * ever needs — see IssuesPage), and a heterogeneous row set breaks a generic
 * per-column sortAccessor cleanly. j/k + Enter traversal is preserved via the
 * same primitive DataTable uses.
 *
 * Columns (Gitea #24): the detector id no longer holds a wide monospace column
 * here — it is an implementation handle, it is on the detail page, and it was
 * spending width that the title needed. What sits there now is Impact, the
 * failed-client-minute figure this product exists to compute; a list that
 * scores issues by user impact and then does not show it buries its own point.
 * The reclaimed width went to the title, which was truncating mid-phrase.
 */

export interface IssueGroup {
  incidentId: number;
  title: string;
  summary: string;
  severity: Severity;
  root: IssueRow;
  symptoms: IssueRow[];
}

export type DisplayRow = { kind: 'issue'; issue: IssueRow } | { kind: 'group'; group: IssueGroup };

// Sev / State / Issue / Entity / Impact / Duration / Last seen / expander.
// Sev is 72px because the widest pill ("Critical") is 68px and was spilling
// under the State pill next to it.
const COLS = '72px 100px minmax(280px,3fr) minmax(130px,1.15fr) 104px 140px 84px 96px';
/** Below this the columns would start crushing each other, so the list scrolls
 *  inside its own container rather than pushing the page sideways. Kept under
 *  1012px, which is what the page's own max-width leaves at a 1280px viewport:
 *  a laptop should read this table without a scrollbar at all. */
const MIN_TABLE_WIDTH = 1006;

export function IssueRowsList({ rows, now }: { rows: DisplayRow[]; now: number }) {
  const navigate = useNavigate();

  function activate(r: DisplayRow) {
    if (r.kind === 'issue') navigate(`/issues/${r.issue.id}`);
    else navigate(`/incidents/${r.group.incidentId}`);
  }

  const nav = useListNavigation(rows.length, (i) => {
    const r = rows[i];
    if (r) activate(r);
  });

  return (
    <div
      className="w-full overflow-x-auto outline-none"
      tabIndex={nav.containerProps.tabIndex}
      role={nav.containerProps.role}
      onKeyDown={nav.containerProps.onKeyDown}
    >
      <div style={{ minWidth: MIN_TABLE_WIDTH }}>
        <div
          className="grid px-3 h-9 items-center t-label font-medium"
          style={{
            gridTemplateColumns: COLS,
            color: 'var(--fg-muted)',
            borderBottom: '1px solid var(--hairline)',
          }}
        >
          <span>Sev</span>
          <span>State</span>
          <span>Issue</span>
          <span>Entity</span>
          <span className="text-right" title={ISSUE_IMPACT_DEFINITION}>
            Impact
          </span>
          <span className="text-right">Duration</span>
          <span className="text-right">Last seen</span>
          <span />
        </div>
        {rows.map((r, i) => {
          const rp = nav.getRowProps(i);
          const rowRef = rp.ref;
          const isActive = rp['aria-selected'];
          return r.kind === 'issue' ? (
            <SoloIssueRow
              key={`i-${r.issue.id}`}
              issue={r.issue}
              now={now}
              rowRef={rowRef}
              isActive={isActive}
              onMouseEnter={rp.onMouseEnter}
              onActivate={() => activate(r)}
            />
          ) : (
            <GroupRow
              key={`g-${r.group.incidentId}`}
              group={r.group}
              now={now}
              rowRef={rowRef}
              isActive={isActive}
              onMouseEnter={rp.onMouseEnter}
              onActivate={() => activate(r)}
            />
          );
        })}
      </div>
    </div>
  );
}

function EntityCell({ entity }: { entity: IssueRow['entity'] }) {
  if (!entity) {
    return (
      <span className="t-body truncate" style={{ color: 'var(--fg-subtle)' }}>
        network-wide
      </span>
    );
  }
  return <EntityLink entity={entity} className="truncate" />;
}

/**
 * The Impact figure, or a dash that says why there isn't one.
 *
 * The dash is load-bearing: the unmeasured case never renders a number, and its
 * reason rides along as hover text *and* as screen-reader text rather than
 * being left for the reader to infer from an em dash.
 */
function ImpactCell({ issue, now }: { issue: IssueRow; now: number }) {
  const { text, zero, note } = impactDisplay(issue, now);

  if (text === null) {
    // `relative` is load-bearing: sr-only is absolutely positioned, and without a
    // positioned ancestor it resolves against the page rather than this cell —
    // escaping the table's own horizontal scroller and dragging the page's
    // scroll width out with it.
    return (
      <span
        className="t-body text-right relative"
        style={{ color: 'var(--fg-subtle)' }}
        title={note}
      >
        <span aria-hidden>—</span>
        <span className="sr-only">{note}</span>
      </span>
    );
  }

  return (
    <span className="text-right whitespace-nowrap" title={note}>
      <span className="t-body tnum" style={{ color: zero ? 'var(--fg-muted)' : 'var(--fg)' }}>
        {text}
      </span>
      <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
        {' fail-min'}
      </span>
    </span>
  );
}

interface RowActivationProps {
  rowRef: RefCallback<HTMLElement>;
  isActive: boolean;
  onMouseEnter: () => void;
  onActivate: () => void;
}

function SoloIssueRow({
  issue,
  now,
  rowRef,
  isActive,
  onMouseEnter,
  onActivate,
}: { issue: IssueRow; now: number } & RowActivationProps) {
  return (
    <div
      ref={rowRef as RefCallback<HTMLDivElement>}
      role="option"
      aria-selected={isActive}
      onMouseEnter={onMouseEnter}
      onClick={onActivate}
      className="grid items-center px-3 cursor-pointer transition-colors"
      style={{
        gridTemplateColumns: COLS,
        height: 44,
        borderBottom: '1px solid var(--hairline)',
        background: isActive ? 'var(--canvas)' : undefined,
      }}
    >
      <SeverityPill severity={issue.severity} />
      <StatePill state={issue.state} severity={issue.severity} />
      <span className="t-body truncate pr-2" style={{ color: 'var(--fg)' }}>
        {issue.title}
      </span>
      <EntityCell entity={issue.entity} />
      <ImpactCell issue={issue} now={now} />
      <span className="t-body tnum text-right pl-3 whitespace-nowrap" style={{ color: 'var(--fg-muted)' }}>
        {ongoingLabel(issue, now)}
      </span>
      <RelativeTime ts={issue.last_seen_ts} mode="relative" className="t-caption tnum text-right" />
      <span />
    </div>
  );
}

function GroupRow({
  group,
  now,
  rowRef,
  isActive,
  onMouseEnter,
  onActivate,
}: { group: IssueGroup; now: number } & RowActivationProps) {
  const [expanded, setExpanded] = useState(false);
  const memberCount = 1 + group.symptoms.length;
  const lastSeen = Math.max(
    group.root.last_seen_ts,
    ...group.symptoms.map((s) => s.last_seen_ts),
  );

  return (
    <div style={{ borderBottom: '1px solid var(--hairline)' }}>
      <div
        ref={rowRef as RefCallback<HTMLDivElement>}
        role="option"
        aria-selected={isActive}
        onMouseEnter={onMouseEnter}
        onClick={onActivate}
        className="grid items-center px-3 py-2 cursor-pointer transition-colors"
        style={{
          gridTemplateColumns: COLS,
          minHeight: 44,
          background: isActive ? 'var(--canvas)' : undefined,
        }}
      >
        <SeverityPill severity={group.severity} />
        <StatePill state={group.root.state} severity={group.severity} />
        <div className="flex flex-col min-w-0 pr-2 gap-0.5">
          <span className="t-body truncate font-medium" style={{ color: 'var(--fg)' }}>
            {group.title}
          </span>
          <span className="t-caption truncate" style={{ color: 'var(--fg-muted)' }}>
            {group.summary}
          </span>
        </div>
        <EntityCell entity={group.root.entity} />
        {/* The group row already speaks for its root everywhere else — entity,
            state, age — so its impact is the root's too, rather than a sum this
            page would have to invent (members routinely share one entity, and
            an unmeasured member would silently understate the total). */}
        <ImpactCell issue={group.root} now={now} />
        <span className="t-body tnum text-right pl-3 whitespace-nowrap" style={{ color: 'var(--fg-muted)' }}>
          ongoing {formatDuration(now - group.root.first_seen_ts)}
        </span>
        <RelativeTime ts={lastSeen} mode="relative" className="t-caption tnum text-right" />
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((v) => !v);
          }}
          aria-expanded={expanded}
          className="inline-flex items-center gap-1 h-[24px] px-2 rounded-control t-caption cursor-pointer transition-colors hover:bg-canvas justify-self-end whitespace-nowrap"
          style={{ border: '1px solid var(--hairline)', color: 'var(--fg-muted)' }}
        >
          {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          {memberCount} issues
        </button>
      </div>

      {expanded && (
        <div className="flex flex-col pb-2 pl-6 pr-3" style={{ gap: 4 }}>
          <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
            {memberCount} issues
          </span>
          <GroupMemberRow issue={group.root} tag="root cause" />
          {group.symptoms.map((s) => (
            <GroupMemberRow key={s.id} issue={s} indent />
          ))}
        </div>
      )}
    </div>
  );
}

function GroupMemberRow({
  issue,
  tag,
  indent = false,
}: {
  issue: IssueRow;
  tag?: string;
  indent?: boolean;
}) {
  return (
    <div className="flex items-center gap-2 py-1" style={{ paddingLeft: indent ? 16 : 0 }}>
      <SeverityGlyph severity={issue.severity} size={10} />
      <Link
        to={`/issues/${issue.id}`}
        onClick={(e) => e.stopPropagation()}
        className="t-caption truncate hover:underline"
        style={{ color: 'var(--fg-muted)' }}
      >
        {issue.title}
      </Link>
      {tag && (
        <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
          ({tag})
        </span>
      )}
      {issue.entity && (
        <span className="t-micro truncate" style={{ color: 'var(--fg-subtle)' }}>
          · <EntityLink entity={issue.entity} muted />
        </span>
      )}
    </div>
  );
}
