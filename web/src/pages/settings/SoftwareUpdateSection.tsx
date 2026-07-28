import { useState, type ReactNode } from 'react';
import { AlertTriangle, ArrowUpCircle, CheckCircle2, Clock, RefreshCw } from 'lucide-react';
import { Button, Card, RelativeTime, Skeleton } from '../../components/ui';
import { forceCheckUpdate, UpdateApiError, type UpdateStatus } from '../../api';
import { HowToUpdatePanel, PipUpdateSheet, useUpdateStatus } from '../../layout/update';

/**
 * Settings → Software update (docs/ARCHITECTURE.md §23).
 *
 * The daemon asks PyPI on its own cadence and the banner in the app shell only
 * speaks when there is news, which leaves silence ambiguous: no banner reads the
 * same whether the check ran ten minutes ago and found nothing, ran hours before
 * the release landed, or has never completed at all. This section is where that
 * silence gets a date on it — the verdict, when the answer was last *completed*
 * ("Checked 4h ago"), and one control that asks PyPI right now:
 *
 *   - Check now — `POST /api/system/update/check`, which bypasses the cadence.
 *     It only ever re-checks a version number; nothing is installed from it. It
 *     is a POST, so on a configured install it is token-gated and `guarded()` in
 *     `api/update.ts` raises the shared just-in-time prompt on a 401.
 *
 * The one thing this must never do is turn a failed check into good news. The
 * endpoint answers **200 with the last cached result** when PyPI is unreachable
 * (`VersionChecker.check_now` logs the failure and falls back), so "did it
 * actually reach PyPI" is read off whether `checked_ts` moved, not off the HTTP
 * status. When it didn't move, the row says so and the previous answer is
 * labelled as the previous answer. Same rule for the two other ways a verdict
 * can be hollow: no check has ever completed (`latest_version === null`), and a
 * build whose version string PyPI's answer can't be compared against.
 *
 * The one-second caveat on `checked_ts`: a background check completing in the
 * very same wall-clock second as a forced one would look like a failure here.
 * That errs toward "unverified", which is the safe direction, and the honest
 * alternative (a per-request success flag on the endpoint) is a backend change.
 */

type Outcome =
  | { kind: 'idle' }
  | { kind: 'checking' }
  /** The forced check reached PyPI: `checked_ts` advanced. */
  | { kind: 'answered' }
  /** 200, but `checked_ts` stood still — PyPI never answered. */
  | { kind: 'unanswered' }
  /** 401 with the prompt dismissed or the token rejected. */
  | { kind: 'unauthorized' }
  | { kind: 'error'; message: string };

/** Whether `latest_version` can be lined up against this build at all. Anything
 * else (a dev/pre-release build, a fourth segment) fails the daemon's strict
 * X.Y.Z parse and comes back `update_available: false` — which must not be
 * shown as "you are on the latest". */
function comparable(status: UpdateStatus): boolean {
  return status.update_available || status.latest_version === status.current_version;
}

function describeError(err: UpdateApiError): string {
  if (err.status === 0) return 'The daemon did not answer.';
  if (err.detail) return `The daemon answered ${err.status}: ${err.detail}`;
  return `The daemon answered ${err.status}.`;
}

/** The cached verdict, phrased in the past tense, for the line that shows what
 * the last completed check found after a failed one. */
function cachedAnswerClause(status: UpdateStatus): string {
  if (status.update_available) {
    return `${status.latest_version} was available, and you are on ${status.current_version}`;
  }
  if (comparable(status)) return `${status.current_version} was the latest release`;
  return `PyPI reported ${status.latest_version}`;
}

export function SoftwareUpdateSection() {
  const { status, loading, error, reload, setStatus } = useUpdateStatus();
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' });
  const [sheet, setSheet] = useState<'none' | 'pip' | 'howto'>('none');

  const runCheck = async () => {
    const before = status?.checked_ts ?? null;
    setOutcome({ kind: 'checking' });
    try {
      const next = await forceCheckUpdate();
      setStatus(next);
      const reachedPypi =
        next.checked_ts !== null && (before === null || next.checked_ts > before);
      setOutcome({ kind: reachedPypi ? 'answered' : 'unanswered' });
    } catch (e) {
      const err = e instanceof UpdateApiError ? e : new UpdateApiError(0, String(e));
      setOutcome(
        err.status === 401
          ? { kind: 'unauthorized' }
          : { kind: 'error', message: describeError(err) },
      );
    }
  };

  if (loading && !status) {
    return (
      <Card className="flex flex-col gap-4">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-10 w-full" />
      </Card>
    );
  }

  if (!status) {
    return (
      <Card className="flex items-center justify-between gap-4 flex-wrap">
        <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
          {error?.status === 0
            ? 'The daemon is unreachable, so the installed version is unknown.'
            : `The update status endpoint returned ${error?.status ?? 'no answer'}.`}
        </span>
        <Button variant="secondary" size="sm" onClick={reload}>
          Retry
        </Button>
      </Card>
    );
  }

  const checking = outcome.kind === 'checking';
  const failed =
    outcome.kind === 'unanswered' ||
    outcome.kind === 'unauthorized' ||
    outcome.kind === 'error';

  return (
    <>
      <Card className="flex flex-col gap-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-1 min-w-0">
            <span className="t-body" style={{ color: 'var(--fg)' }}>
              Software update
            </span>
            <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
              The daemon asks PyPI for a newer release on a schedule. This asks now,
              and installs nothing.
            </span>
          </div>
          <Button
            variant="secondary"
            size="sm"
            className="shrink-0"
            disabled={checking}
            onClick={runCheck}
          >
            <RefreshCw size={15} className={checking ? 'animate-spin' : undefined} aria-hidden />
            {checking ? 'Checking…' : 'Check now'}
          </Button>
        </div>

        <div role="status" aria-live="polite" className="flex flex-col gap-2">
          {/* The verdict. After a failed check it states the failure instead —
              an unverified answer is never dressed up as a verified one. */}
          <div
            className="flex items-start gap-2.5 px-3 py-2.5 rounded-control"
            style={{ background: 'var(--canvas)', border: '1px solid var(--hairline)' }}
          >
            <Verdict status={status} outcome={outcome} />
          </div>

          <p className="t-caption" style={{ color: 'var(--fg-muted)' }}>
            {checking ? (
              'Asking PyPI…'
            ) : failed ? (
              status.checked_ts === null ? (
                'No earlier check has completed, so there is nothing to fall back to.'
              ) : (
                <>
                  Showing the last completed check,{' '}
                  <RelativeTime ts={status.checked_ts} mode="relative" />: {cachedAnswerClause(status)}.
                </>
              )
            ) : status.checked_ts === null ? (
              'Never checked.'
            ) : (
              <RelativeTime ts={status.checked_ts} mode="relative" prefix="Checked " />
            )}
          </p>
        </div>

        {status.update_available && (
          <div
            className="flex items-center justify-between gap-3 pt-3 flex-wrap"
            style={{ borderTop: '1px solid var(--hairline)' }}
          >
            <span className="t-caption" style={{ color: 'var(--fg-muted)' }}>
              {status.self_upgrade_supported
                ? 'Installing backs up the database first and restarts the daemon.'
                : 'This install is updated on the host, not from this page.'}
            </span>
            <div className="flex items-center gap-2">
              {status.release_url && (
                <a
                  href={status.release_url}
                  target="_blank"
                  rel="noreferrer"
                  className="t-caption"
                  style={{ color: 'var(--fg-muted)', textDecoration: 'underline' }}
                >
                  Release notes
                </a>
              )}
              {/* Same rule as the banner: a real Update button only where the
                  daemon can actually self-upgrade, instructions everywhere else. */}
              <Button
                variant={status.self_upgrade_supported ? 'primary' : 'secondary'}
                size="sm"
                onClick={() => setSheet(status.self_upgrade_supported ? 'pip' : 'howto')}
              >
                {status.self_upgrade_supported ? 'Update' : 'How to update'}
              </Button>
            </div>
          </div>
        )}
      </Card>

      {sheet === 'pip' && (
        <PipUpdateSheet
          status={status}
          onClose={() => setSheet('none')}
          onStatusChange={setStatus}
        />
      )}
      {sheet === 'howto' && (
        <HowToUpdatePanel
          method={status.install_method}
          variant={status.variant}
          onClose={() => setSheet('none')}
        />
      )}
    </>
  );
}

function Line({ color, icon, children }: { color: string; icon: ReactNode; children: ReactNode }) {
  return (
    <>
      <span className="shrink-0 mt-0.5" style={{ color }} aria-hidden>
        {icon}
      </span>
      <span className="t-body" style={{ color: 'var(--fg)' }}>
        {children}
      </span>
    </>
  );
}

function Verdict({ status, outcome }: { status: UpdateStatus; outcome: Outcome }) {
  if (outcome.kind === 'unanswered') {
    return (
      <Line color="var(--sev-p3)" icon={<AlertTriangle size={16} />}>
        Couldn't reach PyPI, so this answer is unverified.
      </Line>
    );
  }
  if (outcome.kind === 'unauthorized') {
    return (
      <Line color="var(--sev-p3)" icon={<AlertTriangle size={16} />}>
        Nothing was checked: the access token is required.
      </Line>
    );
  }
  if (outcome.kind === 'error') {
    return (
      <Line color="var(--sev-p1)" icon={<AlertTriangle size={16} />}>
        Nothing was checked. {outcome.message}
      </Line>
    );
  }
  if (status.latest_version === null) {
    return (
      <Line color="var(--sev-neutral)" icon={<Clock size={16} />}>
        No check has completed yet, so the latest release is unknown. You are on{' '}
        {status.current_version}.
      </Line>
    );
  }
  if (status.update_available) {
    return (
      <Line color="var(--accent)" icon={<ArrowUpCircle size={16} />}>
        {status.latest_version} is available. You are on {status.current_version}.
      </Line>
    );
  }
  if (!comparable(status)) {
    return (
      <Line color="var(--sev-p3)" icon={<AlertTriangle size={16} />}>
        PyPI's latest is {status.latest_version}. This build reports{' '}
        {status.current_version}, which can't be compared against it.
      </Line>
    );
  }
  return (
    <Line color="var(--sev-healthy)" icon={<CheckCircle2 size={16} />}>
      You are on {status.current_version}, the latest release.
    </Line>
  );
}
