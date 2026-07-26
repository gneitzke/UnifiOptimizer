import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import {
  SeverityPill,
  StatePill,
  RelativeTime,
  EmptyState,
  cn,
} from '../../components/ui';
import type { InvIssue } from './api';

/**
 * The issues that concern one entity, past and present (docs §Devices/:id: "issues
 * past and present for this entity"). Each row is a whole-row link to the issue
 * detail — the issue surface owns lifecycle/evidence, this is only the entry
 * point (docs §Interaction: navigate for sub-structure). Resolved issues are
 * shown muted below the open ones so history stays visible without competing.
 */

function IssueRow({ issue }: { issue: InvIssue }) {
  const resolved = issue.state === 'resolved';
  return (
    <Link
      to={`/issues/${issue.id}`}
      className={cn(
        'flex items-center gap-3 px-3 h-11 rounded-control transition-colors',
        'hover:bg-canvas focus-visible:bg-canvas',
      )}
      style={{ color: 'var(--fg)' }}
    >
      <SeverityPill severity={issue.severity} glyphOnly />
      <span
        className="flex-1 min-w-0 truncate t-body"
        style={{ color: resolved ? 'var(--fg-muted)' : 'var(--fg)' }}
      >
        {issue.title}
      </span>
      <StatePill state={issue.state} severity={issue.severity} />
      <span className="t-caption tnum shrink-0" style={{ color: 'var(--fg-subtle)' }}>
        <RelativeTime
          ts={resolved ? issue.resolved_ts ?? issue.last_seen_ts : issue.last_seen_ts}
          mode="relative"
        />
      </span>
      <ChevronRight size={15} style={{ color: 'var(--fg-subtle)' }} aria-hidden />
    </Link>
  );
}

export function IssueMiniList({
  open,
  resolved,
}: {
  open: InvIssue[];
  resolved: InvIssue[];
}) {
  if (open.length === 0 && resolved.length === 0) {
    return <EmptyState variant="healthy" title="No issues, past or present" />;
  }

  return (
    <div className="flex flex-col">
      {open.length > 0 ? (
        <div className="flex flex-col">
          {open.map((i) => (
            <IssueRow key={i.id} issue={i} />
          ))}
        </div>
      ) : (
        <EmptyState variant="healthy" title="No open issues" />
      )}

      {resolved.length > 0 && (
        <>
          <div
            className="t-label mt-4 mb-1 px-3"
            style={{ color: 'var(--fg-muted)' }}
          >
            Resolved ({resolved.length})
          </div>
          <div className="flex flex-col" style={{ opacity: 0.85 }}>
            {resolved.map((i) => (
              <IssueRow key={i.id} issue={i} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
