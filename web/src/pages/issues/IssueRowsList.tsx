import { useState, type RefCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { SeverityPill, SeverityGlyph } from '../../components/ui/SeverityPill';
import { StatePill } from '../../components/ui/StatePill';
import { RelativeTime } from '../../components/ui/RelativeTime';
import { useListNavigation } from '../../layout/keyboard/useListNavigation';
import { EntityLink } from '../shared/EntityLink';
import type { IssueRow } from '../shared/api';
import { formatDuration, ongoingLabel } from '../shared/format';
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

const COLS = '56px 100px minmax(220px,2.2fr) minmax(120px,1fr) minmax(130px,1fr) 120px 100px 96px';

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
      <div style={{ minWidth: 960 }}>
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
          <span>Detector</span>
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
      <code className="t-caption truncate" style={{ color: 'var(--fg-muted)' }}>
        {issue.detector_key}
      </code>
      <span className="t-body tnum text-right" style={{ color: 'var(--fg-muted)' }}>
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
        <code className="t-caption truncate" style={{ color: 'var(--fg-muted)' }}>
          {group.root.detector_key}
        </code>
        <span className="t-body tnum text-right" style={{ color: 'var(--fg-muted)' }}>
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
