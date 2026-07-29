import { useState } from 'react';
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, BellOff, Search } from 'lucide-react';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { SeverityPill, SeverityGlyph } from '../../components/ui/SeverityPill';
import { StatePill } from '../../components/ui/StatePill';
import { EmptyState } from '../../components/ui/EmptyState';
import { Skeleton } from '../../components/ui/Skeleton';
import { RelativeTime } from '../../components/ui/RelativeTime';
import { useWsFrames } from '../../api/WsProvider';
import { EntityLink } from '../shared/EntityLink';
import { SuppressedBadge } from '../shared/SuppressedBadge';
import { ProposedFix } from '../issues/ProposedFix';
import { formatDurationLong, isSuppressedNow } from '../shared/format';
import {
  getIncident,
  suppressIncident,
  unsuppressIncident,
  type IncidentMember,
} from '../shared/api';
import { SUPPRESS_OPTIONS } from '../shared/suppress';
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

  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showSuppress, setShowSuppress] = useState(false);

  async function runAction(fn: () => Promise<unknown>) {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
      reload();
    } catch (e) {
      setActionError((e as Error).message || 'Action failed');
    } finally {
      setBusy(false);
      setShowSuppress(false);
    }
  }

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

  // An incident counts as suppressed for the bulk toggle only when every member
  // is suppressed now — the same all-members rule the incidents list uses to drop
  // a fully-muted incident (Gitea #49/#50). A partially-suppressed incident still
  // offers "Suppress incident" so one action parks the whole story.
  const members = [...(root ? [root] : []), ...symptoms];
  const allSuppressed =
    members.length > 0 && members.every((m) => isSuppressedNow(m.issue, now));

  return (
    <div className="px-6 py-6 mx-auto flex flex-col gap-4" style={{ maxWidth: 1000 }}>
      <BackLink />

      {/* Header: the root-cause line */}
      <Card pad="md" className="flex flex-col gap-3">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex flex-col gap-3 min-w-0">
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
          </div>

          {/* Bulk suppress: one action parks the root and every symptom, each as
              its own suppression (Gitea #50). It never touches measured impact —
              the same attention-only mute as the per-issue control. */}
          <div className="flex flex-col items-end gap-1.5 shrink-0">
            <Button
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={() =>
                allSuppressed
                  ? runAction(() => unsuppressIncident(incidentId))
                  : setShowSuppress((v) => !v)
              }
            >
              <BellOff size={14} />
              {allSuppressed ? 'Unsuppress incident' : 'Suppress incident'}
            </Button>
            {showSuppress && !allSuppressed && (
              <div
                className="flex flex-col p-1 rounded-control"
                style={{
                  background: 'var(--elevated)',
                  border: '1px solid var(--hairline)',
                  boxShadow: 'var(--shadow-elevated)',
                }}
              >
                {SUPPRESS_OPTIONS.map((o) => (
                  <button
                    key={o.label}
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      runAction(() =>
                        suppressIncident(incidentId, o.s == null ? undefined : now + o.s),
                      )
                    }
                    className="text-left px-3 py-1.5 rounded t-caption cursor-pointer hover:bg-canvas"
                    style={{ color: 'var(--fg)' }}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            )}
            {actionError && (
              <span className="t-micro" style={{ color: 'var(--sev-p1)' }}>
                {actionError}
              </span>
            )}
          </div>
        </div>
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
          <MemberItem member={root} now={now} emphasise />
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
              <MemberItem member={m} now={now} />
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

function MemberItem({
  member,
  now,
  emphasise = false,
}: {
  member: IncidentMember;
  now: number;
  emphasise?: boolean;
}) {
  const { issue } = member;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-start gap-2">
        <span className="mt-0.5">
          <SeverityGlyph severity={issue.severity} size={12} />
        </span>
        <div className="flex flex-col min-w-0 flex-1 gap-1">
          <span className="flex items-center gap-2 min-w-0">
            <Link
              to={`/issues/${issue.id}`}
              className="t-body truncate hover:underline"
              style={{ color: 'var(--fg)', fontWeight: emphasise ? 600 : 400 }}
            >
              {issue.title}
            </Link>
            {/* The suppressed marker rides the member row so a muted root/symptom
                reads as parked in place (Gitea #50), same badge as the Issues
                list — never a second pattern. */}
            <SuppressedBadge issue={issue} now={now} />
          </span>
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
