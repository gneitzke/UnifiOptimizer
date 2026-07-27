import { Link, Navigate, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, Search } from 'lucide-react';
import { Card } from '../../components/ui/Card';
import { SeverityPill, SeverityGlyph } from '../../components/ui/SeverityPill';
import { StatePill } from '../../components/ui/StatePill';
import { EmptyState } from '../../components/ui/EmptyState';
import { Skeleton } from '../../components/ui/Skeleton';
import { RelativeTime } from '../../components/ui/RelativeTime';
import { useWsFrames } from '../../api/WsProvider';
import { EntityLink } from '../shared/EntityLink';
import { ProposedFix } from '../issues/ProposedFix';
import { formatDurationLong } from '../shared/format';
import { getIncident, type IncidentMember } from '../shared/api';
import { usePageAsync, useNowSeconds } from '../shared/hooks';

/**
 * Incident detail (`/incidents/:id`): the whole story for a genuine incident —
 * the root cause at the top as the thing to fix, the symptoms grouped below each
 * with the human rationale for why it is attributed to this root, and exactly
 * ONE recommended fix (the root's), because clearing the root clears the
 * symptoms. "Incident" is reserved for a 2+ member group (Gitea #21): a deep
 * link that resolves to an incident-of-one redirects straight to its issue —
 * the page already knows `root_issue_id`, so old bookmarks keep working instead
 * of 404ing or showing a page about a "story" with no symptoms to tell.
 */

function SectionCard({
  title,
  children,
  aside,
}: {
  title: string;
  children: React.ReactNode;
  aside?: React.ReactNode;
}) {
  return (
    <Card pad="md" className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="t-section" style={{ color: 'var(--fg)' }}>
          {title}
        </h2>
        {aside}
      </div>
      {children}
    </Card>
  );
}

export function IncidentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const incidentId = Number(id);
  const navigate = useNavigate();
  const now = useNowSeconds();

  const { data, loading, error, reload } = usePageAsync(
    () => getIncident(incidentId),
    [incidentId],
  );

  useWsFrames((frame) => {
    if (frame.type === 'issue_transition') reload();
  });

  if (error) {
    return (
      <div className="px-6 py-6 mx-auto" style={{ maxWidth: 900 }}>
        <BackLink />
        <EmptyState
          variant="no-data"
          title={error.status === 404 ? 'Incident not found' : 'Could not load incident'}
          description={
            error.status === 404
              ? 'This incident id does not exist. It may have been resolved and pruned.'
              : 'The daemon may still be starting, or the API is unreachable.'
          }
          action={{ label: 'Back to dashboard', onClick: () => navigate('/') }}
        />
      </div>
    );
  }

  if (loading && !data) {
    return (
      <div className="px-6 py-6 mx-auto flex flex-col gap-4" style={{ maxWidth: 1000 }}>
        <BackLink />
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (!data) return null;

  const { incident, root, symptoms, recommended_fix, investigation } = data;
  const isGroup = symptoms.length > 0;

  // Not a genuine incident (Gitea #21): redirect to the issue instead of
  // narrating a "story" that is really just one issue talking to itself.
  if (!isGroup) {
    return <Navigate to={`/issues/${incident.root_issue_id}`} replace />;
  }

  const durSecs = Math.max(0, now - incident.first_seen_ts);

  return (
    <div className="px-6 py-6 mx-auto flex flex-col gap-4" style={{ maxWidth: 1000 }}>
      <BackLink />

      {/* Header: the root-cause line */}
      <Card pad="md" className="flex flex-col gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <SeverityPill severity={incident.severity} />
          <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
            {symptoms.length} related symptom{symptoms.length === 1 ? '' : 's'}
          </span>
        </div>
        <h1 className="t-page-title" style={{ color: 'var(--fg)' }}>
          {incident.title}
        </h1>
        {incident.summary && (
          <p className="t-body" style={{ color: 'var(--fg-muted)' }}>
            {incident.summary}
          </p>
        )}
        <span className="t-secondary" style={{ color: 'var(--fg)' }}>
          Ongoing {formatDurationLong(durSecs)} · first seen{' '}
          <RelativeTime ts={incident.first_seen_ts} mode="relative" />
        </span>
      </Card>

      {/* Root cause */}
      {root && (
        <SectionCard
          title="Root cause"
          aside={
            <Link
              to={`/issues/${root.issue.id}`}
              className="inline-flex items-center gap-0.5 t-caption hover:underline"
              style={{ color: 'var(--accent)' }}
            >
              Open issue
              <ArrowRight size={13} />
            </Link>
          }
        >
          <MemberItem member={root} emphasise />
          <p className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
            Fixing this is expected to clear the {symptoms.length} symptom
            {symptoms.length === 1 ? '' : 's'} below.
          </p>
        </SectionCard>
      )}

      {/* The one recommended fix — the root's */}
      <SectionCard title="Recommended fix">
        <p className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
          One fix for the whole incident: remediate the root cause
          {recommended_fix.detector_key ? (
            <>
              {' '}
              (<code style={{ color: 'var(--fg-muted)' }}>{recommended_fix.detector_key}</code>)
            </>
          ) : null}
          .
        </p>
        <ProposedFix
          issueId={recommended_fix.issue_id}
          issueState={root?.issue.state ?? 'active'}
          onChanged={reload}
        />
      </SectionCard>

      {/* Symptoms */}
      <SectionCard title={`Symptoms (${symptoms.length})`}>
        <ul className="flex flex-col gap-3">
          {symptoms.map((m) => (
            <li key={m.issue.id}>
              <MemberItem member={m} />
            </li>
          ))}
        </ul>
      </SectionCard>

      {/* Investigation hook */}
      <SectionCard title="Investigate">
        <p className="t-body" style={{ color: 'var(--fg-muted)' }}>
          Narrate this incident end to end by investigating its root cause. The
          dossier includes the symptoms attributed to it.
        </p>
        <Link
          to={`/issues/${investigation.issue_id}`}
          className="inline-flex items-center gap-1.5 t-body hover:underline self-start"
          style={{ color: 'var(--accent)' }}
        >
          <Search size={14} />
          Investigate the root cause
        </Link>
      </SectionCard>
    </div>
  );
}

function MemberItem({ member, emphasise = false }: { member: IncidentMember; emphasise?: boolean }) {
  const { issue } = member;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-start gap-2">
        <span className="mt-0.5">
          <SeverityGlyph severity={issue.severity} size={12} />
        </span>
        <div className="flex flex-col min-w-0 flex-1 gap-1">
          <Link
            to={`/issues/${issue.id}`}
            className="t-body hover:underline"
            style={{ color: 'var(--fg)', fontWeight: emphasise ? 600 : 400 }}
          >
            {issue.title}
          </Link>
          <div className="flex items-center gap-2 flex-wrap">
            <StatePill state={issue.state} severity={issue.severity} />
            <code className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
              {issue.detector_key}
            </code>
            {member.entity && (
              <span className="t-caption" style={{ color: 'var(--fg-muted)' }}>
                <EntityLink entity={member.entity} muted />
              </span>
            )}
          </div>
          {member.role === 'symptom' && member.rationale && (
            <p className="t-caption" style={{ color: 'var(--fg-muted)' }}>
              {member.rationale}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/"
      className="inline-flex items-center gap-1 t-caption self-start hover:underline"
      style={{ color: 'var(--fg-muted)' }}
    >
      <ArrowLeft size={14} />
      Dashboard
    </Link>
  );
}
