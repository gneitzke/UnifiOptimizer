import { useState } from 'react';
import { AlertTriangle, CheckCircle2, RefreshCw, Rocket } from 'lucide-react';
import { Button } from '../../components/ui/Button';
import { applyUpdate, isUpgradeInProgress, UpdateApiError, type UpdateStatus } from '../../api/update';
import { PIP_PHASE_LABEL, phaseProgress } from './phaseCopy';

/**
 * The pip self-upgrade confirmation sheet (docs/ARCHITECTURE.md §23).
 *
 * A modal, not the slim banner itself — it exists only to get explicit consent
 * before `POST /api/system/update/apply` fires, and to narrate the runner's
 * progress for anyone who stays and watches. `status` is the parent's live,
 * polled state, so once the apply call succeeds this component keeps rendering
 * from the SAME data the banner uses — closing this sheet never stops the
 * banner from tracking the upgrade to completion.
 *
 * Local state only covers the request itself (idle / in flight / rejected);
 * "is an upgrade actually running" is read straight off `status.upgrade_state`,
 * so a reload while one is in flight reopens straight into the progress view
 * rather than re-asking for consent.
 */

type RequestPhase = 'confirm' | 'starting' | 'error';

function initialRequestPhase(status: UpdateStatus): RequestPhase {
  const state = status.upgrade_state;
  if (state && isUpgradeInProgress(state.phase) && state.target_version === status.latest_version) {
    return 'starting'; // already running — render the tracking view immediately below
  }
  return 'confirm';
}

export function PipUpdateSheet({
  status,
  onClose,
  onStatusChange,
}: {
  status: UpdateStatus;
  onClose: () => void;
  onStatusChange: (next: UpdateStatus) => void;
}) {
  const [requestPhase, setRequestPhase] = useState<RequestPhase>(() => initialRequestPhase(status));
  const [errorDetail, setErrorDetail] = useState<string | null>(null);

  const target = status.latest_version;
  const state = status.upgrade_state;
  const tracking =
    requestPhase !== 'confirm' &&
    requestPhase !== 'error' &&
    !!state &&
    state.target_version === target;

  const confirm = async () => {
    if (!target) return;
    setRequestPhase('starting');
    setErrorDetail(null);
    try {
      const next = await applyUpdate(target);
      onStatusChange(next);
      // requestPhase stays 'starting' — `tracking` above now reads live
      // progress off `status.upgrade_state` as the parent keeps polling.
    } catch (e) {
      const err = e instanceof UpdateApiError ? e : new UpdateApiError(0, String(e));
      setErrorDetail(err.detail ?? err.message);
      setRequestPhase('error');
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-6 py-10"
      style={{ background: 'color-mix(in srgb, var(--canvas) 68%, transparent)' }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={onKeyDown}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="update-sheet-title"
        className="w-full max-w-[440px] rounded-card p-5 flex flex-col"
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--hairline)',
          boxShadow: 'var(--shadow-elevated)',
        }}
      >
        {tracking && state ? (
          <TrackingView state={state} onClose={onClose} onTryAgain={() => setRequestPhase('confirm')} />
        ) : (
          <ConfirmView
            status={status}
            requestPhase={requestPhase}
            errorDetail={errorDetail}
            onConfirm={confirm}
            onCancel={onClose}
          />
        )}
      </div>
    </div>
  );
}

function ConfirmView({
  status,
  requestPhase,
  errorDetail,
  onConfirm,
  onCancel,
}: {
  status: UpdateStatus;
  requestPhase: RequestPhase;
  errorDetail: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const lastAttempt = status.upgrade_state;
  const lastAttemptFailed =
    requestPhase === 'confirm' &&
    lastAttempt &&
    (lastAttempt.phase === 'failed' || lastAttempt.phase === 'rolled_back') &&
    lastAttempt.target_version === status.latest_version;

  return (
    <>
      <div className="flex items-start gap-3 mb-3">
        <span
          aria-hidden
          className="inline-flex items-center justify-center w-8 h-8 rounded-control shrink-0"
          style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
        >
          <Rocket size={16} />
        </span>
        <div className="flex flex-col gap-1">
          <h1 id="update-sheet-title" className="t-section" style={{ color: 'var(--fg)' }}>
            Update to {status.latest_version}?
          </h1>
          <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
            You're on {status.current_version}.
          </p>
        </div>
      </div>

      <p className="t-body mb-3" style={{ color: 'var(--fg)' }}>
        This backs up the database, installs the new version alongside the current
        one, and tests it against a copy of your data before switching over. The
        whole process takes about a minute. If anything goes wrong, it's rolled
        back automatically and nothing changes.
      </p>

      {lastAttemptFailed && (
        <p
          className="t-caption mb-3 flex items-start gap-1.5"
          style={{ color: 'var(--sev-p2)' }}
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
          <span>
            A previous attempt didn't complete
            {lastAttempt?.error ? `: ${lastAttempt.error}` : '.'} Nothing was left
            half-upgraded, so it is safe to try again.
          </span>
        </p>
      )}

      {requestPhase === 'error' && (
        <p role="alert" className="t-caption mb-3" style={{ color: 'var(--sev-p1)' }}>
          Couldn't start the update{errorDetail ? `: ${errorDetail}` : '.'}
        </p>
      )}

      <div className="flex items-center justify-end gap-2 mt-1">
        <Button variant="ghost" size="md" onClick={onCancel} disabled={requestPhase === 'starting'}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="md"
          onClick={onConfirm}
          disabled={requestPhase === 'starting' || !status.latest_version}
        >
          {requestPhase === 'starting' ? 'Starting…' : 'Update now'}
        </Button>
      </div>
    </>
  );
}

function TrackingView({
  state,
  onClose,
  onTryAgain,
}: {
  state: NonNullable<UpdateStatus['upgrade_state']>;
  onClose: () => void;
  onTryAgain: () => void;
}) {
  const label = PIP_PHASE_LABEL[state.phase];
  const progress = phaseProgress(state.phase);

  if (state.phase === 'done') {
    return (
      <TerminalView
        icon={<CheckCircle2 size={16} />}
        tone="var(--sev-healthy)"
        title="Update complete"
        detail={`Now running ${state.target_version}.`}
        primary={{ label: 'Close', onClick: onClose }}
      />
    );
  }

  if (state.phase === 'failed' || state.phase === 'rolled_back') {
    const restored = state.phase === 'rolled_back';
    return (
      <TerminalView
        icon={<AlertTriangle size={16} />}
        tone="var(--sev-p1)"
        title={restored ? 'Update rolled back' : 'Update failed'}
        detail={
          restored
            ? `Automatically restored ${state.from_version}. Your data was restored too.${
                state.error ? ` (${state.error})` : ''
              }`
            : `Nothing changed. The install is still ${state.from_version}.${
                state.error ? ` (${state.error})` : ''
              }`
        }
        primary={{ label: 'Try again', onClick: onTryAgain }}
        secondary={{ label: 'Close', onClick: onClose }}
      />
    );
  }

  // An in-progress phase.
  return (
    <>
      <div className="flex items-start gap-3 mb-4">
        <span
          aria-hidden
          className="inline-flex items-center justify-center w-8 h-8 rounded-control shrink-0"
          style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
        >
          <RefreshCw size={16} className="animate-spin" />
        </span>
        <div className="flex flex-col gap-1">
          <h1 className="t-section" style={{ color: 'var(--fg)' }}>
            Updating to {state.target_version}
          </h1>
          <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
            {label}
          </p>
        </div>
      </div>

      <div
        role="progressbar"
        aria-valuenow={Math.round(progress * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-1.5 rounded-full overflow-hidden mb-4"
        style={{ background: 'var(--hairline)' }}
      >
        <div
          className="h-full rounded-full transition-[width]"
          style={{ width: `${progress * 100}%`, background: 'var(--accent)' }}
        />
      </div>

      <p className="t-caption mb-4" style={{ color: 'var(--fg-subtle)' }}>
        This takes about a minute, including a brief restart. You can close this;
        the banner keeps tracking it.
      </p>

      <div className="flex items-center justify-end">
        <Button variant="ghost" size="md" onClick={onClose}>
          Close
        </Button>
      </div>
    </>
  );
}

function TerminalView({
  icon,
  tone,
  title,
  detail,
  primary,
  secondary,
}: {
  icon: React.ReactNode;
  tone: string;
  title: string;
  detail: string;
  primary: { label: string; onClick: () => void };
  secondary?: { label: string; onClick: () => void };
}) {
  return (
    <>
      <div className="flex items-start gap-3 mb-4">
        <span
          aria-hidden
          className="inline-flex items-center justify-center w-8 h-8 rounded-control shrink-0"
          style={{ background: `color-mix(in srgb, ${tone} 16%, transparent)`, color: tone }}
        >
          {icon}
        </span>
        <div className="flex flex-col gap-1">
          <h1 className="t-section" style={{ color: 'var(--fg)' }}>
            {title}
          </h1>
          <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
            {detail}
          </p>
        </div>
      </div>
      <div className="flex items-center justify-end gap-2">
        {secondary && (
          <Button variant="ghost" size="md" onClick={secondary.onClick}>
            {secondary.label}
          </Button>
        )}
        <Button variant="primary" size="md" onClick={primary.onClick}>
          {primary.label}
        </Button>
      </div>
    </>
  );
}
