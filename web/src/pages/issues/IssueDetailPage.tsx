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
import { formatDurationLong, humanizeKey, issueDurationSeconds } from '../shared/format';
import { ackIssue, getIssue, snoozeIssue } from '../shared/api';
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
 * chart from the evidence's series hints, the ack/snooze operator actions (which
 * mute notifications only — never touch evaluation, §7), and the LLM
 * investigation section (dossier → provider → response, §10).
 */

const SNOOZE_OPTIONS = [
  { label: '1 hour', s: 3_600 },
  { label: '8 hours', s: 28_800 },
  { label: '24 hours', s: 86_400 },
  { label: '3 days', s: 259_200 },
  { label: '7 days', s: 604_800 },
];

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
  const [showSnooze, setShowSnooze] = useState(false);
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
      setShowSnooze(false);
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

  const { issue, entity, evidence, confounders, events, incident } = data;
  const durSecs = issueDurationSeconds(issue, now);
  const snoozed = issue.snooze_until_ts != null && issue.snooze_until_ts > now;
  const hints = metricHintsForIssue(issue);
  const isOpen = issue.state !== 'resolved';

  return (
    <div className="px-6 py-6 mx-auto flex flex-col gap-4" style={{ maxWidth: 1100 }}>
      <BackLink />

      {/* Header */}
      <Card pad="md" className="flex flex-col gap-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex flex-col gap-2 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <SeverityPill severity={issue.severity} />
              <StatePill state={issue.state} severity={issue.severity} />
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
            </span>
            {incident && (
              <Link
                to={`/incidents/${incident.id}`}
                className="inline-flex items-center gap-1.5 t-caption self-start hover:underline"
                style={{ color: 'var(--accent)' }}
              >
                <Layers size={13} />
                Part of: {incident.title}
                {incident.role === 'root' ? (
                  <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
                    (root cause)
                  </span>
                ) : (
                  <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
                    (symptom)
                  </span>
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
                  onClick={() => setShowSnooze((v) => !v)}
                >
                  <BellOff size={14} />
                  {snoozed ? 'Snoozed' : 'Snooze'}
                </Button>
              </div>
              {snoozed && (
                <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
                  until <RelativeTime ts={issue.snooze_until_ts} mode="at" />
                  {' · '}
                  <button
                    type="button"
                    className="hover:underline cursor-pointer"
                    style={{ color: 'var(--accent)' }}
                    disabled={busy}
                    onClick={() => runAction(() => snoozeIssue(issueId, now))}
                  >
                    clear
                  </button>
                </span>
              )}
              {showSnooze && (
                <div
                  className="flex flex-col p-1 rounded-control"
                  style={{ background: 'var(--elevated)', border: '1px solid var(--hairline)', boxShadow: 'var(--shadow-elevated)' }}
                >
                  {SNOOZE_OPTIONS.map((o) => (
                    <button
                      key={o.s}
                      type="button"
                      disabled={busy}
                      onClick={() => runAction(() => snoozeIssue(issueId, now + o.s))}
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
            <EvidenceView evidence={evidence} />
          </SectionCard>

          <SectionCard title="Related metrics">
            <MetricEvidenceChart hints={hints} />
          </SectionCard>

          <SectionCard title="Proposed fix">
            <ProposedFix issueId={issueId} onChanged={reload} />
          </SectionCard>

          <SectionCard title="Investigation">
            <InvestigationPanel issueId={issueId} onInvestigated={reload} />
          </SectionCard>
        </div>

        <div className="flex flex-col gap-4">
          <SectionCard title="Lifecycle">
            <LifecycleTrail events={events} />
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
                      {humanizeKey(c)}
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
