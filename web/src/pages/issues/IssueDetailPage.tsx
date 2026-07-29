import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, BellOff, Check, Layers } from 'lucide-react';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { SeverityPill } from '../../components/ui/SeverityPill';
import { StatePill } from '../../components/ui/StatePill';
import { EmptyState } from '../../components/ui/EmptyState';
import { Skeleton } from '../../components/ui/Skeleton';
import { RelativeTime } from '../../components/ui/RelativeTime';
import { useWsFrames } from '../../api/WsProvider';
import { EntityLink } from '../shared/EntityLink';
import {
  clearProgressLabel,
  clearProgressNote,
  formatDurationLong,
  humanizeKey,
  impactDisplay,
  isSuppressedNow,
  issueDurationSeconds,
  recurrencePhrase,
  suppressionEscalationVoid,
  suppressionNote,
} from '../shared/format';
import { ackIssue, getIssue, suppressIssue, unsuppressIssue } from '../shared/api';
import { SUPPRESS_OPTIONS } from '../shared/suppress';
import { usePageAsync, useNowSeconds } from '../shared/hooks';
import { EvidenceView } from './EvidenceView';
import { InvestigationPanel } from './InvestigationPanel';
import { LifecycleTrail } from './LifecycleTrail';
import { MetricEvidenceChart } from './MetricEvidenceChart';
import { ProposedFix } from './ProposedFix';
import { metricHintsForIssue } from './metricHints';

/**
 * Issue detail (`/issues/:id`): the full lifecycle trail, evidence as compact
 * labeled numbers, the confounders the detector ruled out, the related metric
 * chart from the evidence's series hints, the operator actions — Acknowledge (a
 * provenance stamp, no behavioural effect) and Suppress (the one attention mute,
 * with an optional expiry; it parks counts/alerts and never touches measured
 * impact, Gitea #49) — and the LLM investigation section (§10).
 */

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card pad="md" className="flex flex-col gap-3">
      <h2 className="t-section" style={{ color: 'var(--fg)' }}>
        {title}
      </h2>
      {children}
    </Card>
  );
}

function MetaItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
        {label}
      </span>
      <span className="t-body" style={{ color: 'var(--fg)' }}>
        {children}
      </span>
    </div>
  );
}

export function IssueDetailPage() {
  const { id } = useParams<{ id: string }>();
  const issueId = Number(id);
  const navigate = useNavigate();

  const { data, loading, error, reload } = usePageAsync(
    () => getIssue(issueId),
    [issueId],
  );

  useWsFrames((frame) => {
    if (frame.type === 'issue_transition' && frame.issue_id === issueId) reload();
  });

  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showSuppress, setShowSuppress] = useState(false);
  const now = useNowSeconds();

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
          title={error.status === 404 ? 'Issue not found' : 'Could not load issue'}
          description={
            error.status === 404
              ? 'This issue id does not exist. It may have been pruned.'
              : 'The daemon may still be starting, or the API is unreachable.'
          }
          action={{ label: 'Back to issues', onClick: () => navigate('/issues') }}
        />
      </div>
    );
  }

  if (loading && !data) {
    return (
      <div className="px-6 py-6 mx-auto flex flex-col gap-4" style={{ maxWidth: 1100 }}>
        <BackLink />
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!data) return null;

  const { issue, entity, evidence, evidence_layout, confounders, confounder_notes, events, incident } =
    data;
  const durSecs = issueDurationSeconds(issue, now);
  const impact = impactDisplay(issue, now);
  const suppressed = isSuppressedNow(issue, now);
  const hints = metricHintsForIssue(issue);
  const isOpen = issue.state !== 'resolved';
  const recurrence = recurrencePhrase(issue);

  return (
    <div className="px-6 py-6 mx-auto flex flex-col gap-4" style={{ maxWidth: 1100 }}>
      <BackLink />

      {/* Header */}
      <Card pad="md" className="flex flex-col gap-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex flex-col gap-2 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <SeverityPill severity={issue.severity} />
              {/* Width is free here, so the pill carries the streak itself
                  rather than a caption under it (Gitea #39). */}
              <StatePill
                state={issue.state}
                severity={issue.severity}
                progress={clearProgressLabel(issue)}
                title={clearProgressNote(issue) ?? undefined}
              />
              {issue.fix_state && (
                <span
                  className="inline-flex items-center h-[20px] px-1.5 rounded-full text-[12px] font-medium"
                  style={{ background: 'var(--sev-neutral-fill)', color: 'var(--fg-muted)' }}
                >
                  fix {issue.fix_state}
                </span>
              )}
            </div>
            <h1 className="t-page-title" style={{ color: 'var(--fg)' }}>
              {issue.title}
            </h1>
            <span
              className="t-secondary"
              style={{ color: issue.state === 'resolved' ? 'var(--fg-muted)' : 'var(--fg)' }}
            >
              {issue.state === 'resolved' ? 'Lasted' : 'Ongoing'} {formatDurationLong(durSecs)}
              {issue.occurrences > 1 ? ` · ${issue.occurrences} occurrences` : ''}
              {/* Two different numbers, and the difference is the point:
                  occurrences counts every cycle this fired, recurrence counts
                  the times it came back after starting to clear (Gitea #39). */}
              {recurrence ? ` · ${recurrence}` : ''}
            </span>
            {/* Rendered only for a genuine incident (2+ members, Gitea #21) —
                an incident-of-one has symptom_count 0, and showing a line
                that links to a "story" containing only this same issue is a
                self-link, not information. Role-specific copy: the root
                names how many symptoms it explains; a symptom just names
                its root. */}
            {incident && incident.symptom_count > 0 && (
              <Link
                to={`/incidents/${incident.id}`}
                className="inline-flex items-center gap-1.5 t-caption self-start hover:underline"
                style={{ color: 'var(--accent)' }}
              >
                <Layers size={13} />
                {incident.role === 'root' ? (
                  <>
                    Root cause of: {incident.title} ({incident.symptom_count} symptom
                    {incident.symptom_count === 1 ? '' : 's'})
                  </>
                ) : (
                  <>Symptom of: {incident.title}</>
                )}
              </Link>
            )}
          </div>

          {isOpen && (
            <div className="flex flex-col items-end gap-1.5">
              <div className="flex items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy}
                  onClick={() => runAction(() => ackIssue(issueId))}
                >
                  <Check size={14} />
                  {issue.ack_ts ? 'Acknowledged' : 'Acknowledge'}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy}
                  onClick={() =>
                    suppressed
                      ? runAction(() => unsuppressIssue(issueId))
                      : setShowSuppress((v) => !v)
                  }
                >
                  <BellOff size={14} />
                  {suppressed ? 'Unsuppress' : 'Suppress'}
                </Button>
              </div>
              {showSuppress && !suppressed && (
                <div
                  className="flex flex-col p-1 rounded-control"
                  style={{ background: 'var(--elevated)', border: '1px solid var(--hairline)', boxShadow: 'var(--shadow-elevated)' }}
                >
                  {SUPPRESS_OPTIONS.map((o) => (
                    <button
                      key={o.label}
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        runAction(() =>
                          suppressIssue(issueId, o.s == null ? undefined : now + o.s),
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
          )}
        </div>

        {/* Suppressed banner: facts intact, attention claim visibly parked. The
            load-bearing clause is "measured impact unchanged" — the mute never
            un-suffers the client-minutes this issue cost (Gitea #49). */}
        {suppressed && (
          <div
            className="flex items-center gap-2 flex-wrap px-3 py-2 rounded-control t-caption"
            style={{ background: 'var(--sev-neutral-fill)', color: 'var(--fg-muted)' }}
            title={suppressionNote(issue, now)}
          >
            <BellOff size={13} className="shrink-0" style={{ color: 'var(--fg-subtle)' }} />
            <span>
              Suppressed <RelativeTime ts={issue.suppressed_ts ?? now} mode="relative" />
              {issue.suppress_until_ts != null ? (
                <>
                  {' · until '}
                  <RelativeTime ts={issue.suppress_until_ts} mode="at" />
                </>
              ) : (
                ' · until unsuppressed'
              )}
              {' · excluded from counts and alerts; measured impact unchanged'}
            </span>
            <button
              type="button"
              className="hover:underline cursor-pointer"
              style={{ color: 'var(--accent)' }}
              disabled={busy}
              onClick={() => runAction(() => unsuppressIssue(issueId))}
            >
              Unsuppress
            </button>
          </div>
        )}

        {/* Meta grid */}
        <div
          className="grid gap-4 pt-3"
          style={{
            borderTop: '1px solid var(--hairline)',
            gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
          }}
        >
          <MetaItem label="Entity">
            {entity ? <EntityLink entity={entity} /> : <span style={{ color: 'var(--fg-subtle)' }}>network-wide</span>}
          </MetaItem>
          {/* The figures the Issues list is read by, repeated here with the
              sentences that qualify them — otherwise the column is a number the
              reader can click on and learn nothing more about. Same
              `impactDisplay` the list uses, so the two can never drift, and the
              two axes stay two figures here as well (Gitea #36). */}
          <MetaItem label="Impact">
            {impact.primary === null ? (
              <span className="relative" style={{ color: 'var(--fg-subtle)' }} title={impact.note}>
                <span aria-hidden>—</span>
                <span className="sr-only">{impact.note}</span>
              </span>
            ) : (
              <span className="relative flex flex-col gap-0.5" title={impact.note}>
                <span
                  aria-hidden
                  className="tnum"
                  style={{ color: impact.primary.zero ? 'var(--fg-muted)' : 'var(--fg)' }}
                >
                  {impact.primary.text}
                </span>
                {impact.secondary && (
                  <span aria-hidden className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
                    {impact.secondary.text}
                  </span>
                )}
                <span className="sr-only">{impact.note}</span>
              </span>
            )}
          </MetaItem>
          <MetaItem label="Detector">
            <code className="t-caption" style={{ color: 'var(--fg-muted)' }}>
              {issue.detector_key}
            </code>
          </MetaItem>
          <MetaItem label="First seen">
            <RelativeTime ts={issue.first_seen_ts} mode="relative" />
          </MetaItem>
          <MetaItem label="Last seen">
            <RelativeTime ts={issue.last_seen_ts} mode="relative" />
          </MetaItem>
          {issue.ack_ts != null && (
            <MetaItem label="Acknowledged">
              <RelativeTime ts={issue.ack_ts} mode="relative" />
            </MetaItem>
          )}
          <MetaItem label="Fingerprint">
            <code className="t-micro" style={{ color: 'var(--fg-subtle)' }} title={issue.fingerprint}>
              {issue.fingerprint.slice(0, 12)}
            </code>
          </MetaItem>
        </div>
      </Card>

      {/* Body: evidence + metrics on the left, lifecycle + confounders on the right */}
      <div className="grid gap-4 lg:grid-cols-[3fr_2fr] items-start">
        <div className="flex flex-col gap-4">
          <SectionCard title="Evidence">
            <EvidenceView evidence={evidence} layout={evidence_layout} />
          </SectionCard>

          <SectionCard title="Related metrics">
            <MetricEvidenceChart hints={hints} />
          </SectionCard>

          <SectionCard title="Proposed fix">
            <ProposedFix issueId={issueId} issueState={issue.state} onChanged={reload} />
          </SectionCard>

          <SectionCard title="Investigation">
            <InvestigationPanel issueId={issueId} onInvestigated={reload} />
          </SectionCard>
        </div>

        <div className="flex flex-col gap-4">
          <SectionCard title="Lifecycle">
            <LifecycleTrail
              events={events}
              currentState={issue.state}
              clearStreak={issue.clear_streak}
              escalationVoid={suppressionEscalationVoid(issue, now)}
            />
          </SectionCard>

          <SectionCard title="Ruled out">
            {confounders.length === 0 ? (
              <p className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
                No confounders were recorded for this detector.
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {confounders.map((c) => (
                  <li key={c} className="flex items-start gap-2">
                    <Check size={15} className="mt-0.5 shrink-0" style={{ color: 'var(--sev-healthy)' }} />
                    <span className="t-body" style={{ color: 'var(--fg)' }}>
                      {confounder_notes[c] ?? humanizeKey(c)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
        </div>
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/issues"
      className="inline-flex items-center gap-1 t-caption self-start hover:underline"
      style={{ color: 'var(--fg-muted)' }}
    >
      <ArrowLeft size={14} />
      Issues
    </Link>
  );
}
