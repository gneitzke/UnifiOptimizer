import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, ChevronDown } from 'lucide-react';
import { SeverityPill, SeverityGlyph } from '../../components/ui/SeverityPill';
import { StatePill } from '../../components/ui/StatePill';
import { Skeleton } from '../../components/ui/Skeleton';
import { EntityLink } from '../shared/EntityLink';
import { formatDuration } from '../shared/format';
import {
  getIncident,
  entityLabel,
  type IncidentSummary,
  type IncidentMember,
} from '../shared/api';

/**
 * One incident, rendered as a row: root-cause line + entity/duration, with a
 * "+N related" expander that lazily loads and reveals the symptoms. A standalone
 * issue (incident-of-one) links straight to the issue and shows its state pill.
 * Shared verbatim by the dashboard's Active-incidents card and the full
 * `/incidents` list so the two never drift (§17: the operator sees the same
 * incident affordance wherever incidents appear).
 */
export function IncidentRow({
  incident,
  now,
}: {
  incident: IncidentSummary;
  now: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const [symptoms, setSymptoms] = useState<IncidentMember[] | null>(null);
  const [loadingSymptoms, setLoadingSymptoms] = useState(false);

  const isGroup = incident.symptom_count > 0;
  const root = incident.root;
  const headTitle = root?.title ?? incident.title;
  const href = isGroup ? `/incidents/${incident.id}` : `/issues/${incident.root_issue_id}`;
  const ongoing = `ongoing ${formatDuration(now - incident.first_seen_ts)}`;

  async function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && symptoms === null && !loadingSymptoms) {
      setLoadingSymptoms(true);
      try {
        const detail = await getIncident(incident.id);
        setSymptoms(detail.symptoms);
      } catch {
        setSymptoms([]);
      } finally {
        setLoadingSymptoms(false);
      }
    }
  }

  return (
    <li style={{ borderTop: '1px solid var(--hairline)' }}>
      <div className="flex items-center gap-2 py-2">
        <SeverityPill severity={incident.severity} glyphOnly />
        <Link
          to={href}
          className="flex flex-col min-w-0 flex-1 -my-1 py-1 rounded transition-colors hover:bg-canvas"
        >
          <span className="t-body truncate" style={{ color: 'var(--fg)' }}>
            {headTitle}
          </span>
          <span className="t-caption truncate" style={{ color: 'var(--fg-muted)' }}>
            {isGroup
              ? incident.summary || `${incident.symptom_count} related symptom(s)`
              : `${root?.entity ? entityLabel(root.entity) : 'network-wide'} · ${ongoing}`}
          </span>
        </Link>
        {isGroup ? (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={expanded}
            className="inline-flex items-center gap-1 h-[24px] px-2 rounded-control t-caption cursor-pointer transition-colors hover:bg-canvas whitespace-nowrap"
            style={{ border: '1px solid var(--hairline)', color: 'var(--fg-muted)' }}
          >
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}+
            {incident.symptom_count} related
          </button>
        ) : (
          root && <StatePill state={root.state} severity={incident.severity} />
        )}
      </div>

      {isGroup && expanded && (
        <ul className="flex flex-col pb-2 pl-6" style={{ gap: 2 }}>
          {loadingSymptoms && symptoms === null ? (
            <Skeleton className="h-5 w-2/3" />
          ) : (
            (symptoms ?? []).map((m) => (
              <li key={m.issue.id} className="flex items-center gap-2 py-1">
                <SeverityGlyph severity={m.issue.severity} size={10} />
                <Link
                  to={`/issues/${m.issue.id}`}
                  className="t-caption truncate hover:underline"
                  style={{ color: 'var(--fg-muted)' }}
                >
                  {m.issue.title}
                </Link>
                {m.entity && (
                  <span className="t-micro truncate" style={{ color: 'var(--fg-subtle)' }}>
                    · <EntityLink entity={m.entity} muted />
                  </span>
                )}
              </li>
            ))
          )}
        </ul>
      )}
    </li>
  );
}
