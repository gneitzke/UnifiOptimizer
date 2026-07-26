import { useState } from 'react';
import { ArrowUpCircle, RefreshCw, X } from 'lucide-react';
import { Button } from '../../components/ui/Button';
import { dismissUpdate, isUpgradeInProgress, type UpdateStatus } from '../../api/update';
import { useUpdateStatus } from './useUpdateStatus';
import { PipUpdateSheet } from './PipUpdateSheet';
import { HowToUpdatePanel } from './HowToUpdatePanel';
import { PIP_PHASE_LABEL, phaseProgress } from './phaseCopy';

/**
 * The self-update banner (docs/ARCHITECTURE.md §23): a slim dismissible bar
 * above the dashboard nav, never a modal on its own. Mounted once in
 * `AppShell`, above the sidebar/header row, so it survives route changes.
 *
 * Three states, in priority order:
 *
 *   1. **An upgrade is actually running** (`upgrade_state.phase` in the
 *      runner's in-progress set) — a narrow progress row, driven purely by
 *      the polled server state, so it renders correctly even after a page
 *      reload mid-upgrade. Clicking it opens the same tracking view the
 *      confirmation sheet shows.
 *   2. **A newer version is available**, not skipped, not snoozed — the
 *      classic banner: version line, the one real action (Update for a
 *      self-upgrading pip install, "How to update" everywhere else per
 *      `self_upgrade_supported` — never a fake button), "Skip this version",
 *      and a close (✕) that snoozes 7 days.
 *   3. **Nothing to say** — renders nothing. This includes the first load
 *      (no flash-of-banner before the first response) and a fetch failure
 *      (a broken update check must never itself look like a broken dashboard).
 *
 * Skip/snooze are server-side (`POST /system/update/dismiss`), never
 * `localStorage` — the dismissal follows the install, not the browser.
 */

export function UpdateBanner() {
  const { status, loading, setStatus } = useUpdateStatus();
  const [sheet, setSheet] = useState<'none' | 'pip' | 'howto'>('none');
  const [busy, setBusy] = useState<'skip' | 'snooze' | null>(null);

  if (loading && !status) return null;
  if (!status) return null;

  const state = status.upgrade_state;
  const active = isUpgradeInProgress(state?.phase);
  const skipped = status.skipped_version !== null && status.skipped_version === status.latest_version;
  const snoozed = status.snoozed_until !== null && Date.now() / 1000 < status.snoozed_until;
  const showAvailable = status.update_available && !active && !skipped && !snoozed;

  if (!active && !showAvailable) return null;

  const dismiss = async (mode: 'skip' | 'snooze') => {
    setBusy(mode);
    try {
      setStatus(await dismissUpdate(mode));
    } catch {
      // A cancelled token prompt or a rejected token: leave the banner as-is,
      // the operator can try again.
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div
        className="no-print shrink-0 relative"
        style={{ borderBottom: '1px solid var(--hairline)', background: 'var(--surface)' }}
      >
        {active && state ? (
          <ActiveRow state={state} onOpen={() => setSheet('pip')} />
        ) : (
          <AvailableRow
            status={status}
            busy={busy}
            onPrimary={() => setSheet(status.self_upgrade_supported ? 'pip' : 'howto')}
            onSkip={() => dismiss('skip')}
            onSnooze={() => dismiss('snooze')}
          />
        )}
      </div>

      {sheet === 'pip' && (
        <PipUpdateSheet status={status} onClose={() => setSheet('none')} onStatusChange={setStatus} />
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

function ActiveRow({
  state,
  onOpen,
}: {
  state: NonNullable<UpdateStatus['upgrade_state']>;
  onOpen: () => void;
}) {
  const progress = phaseProgress(state.phase);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen();
        }
      }}
      className="flex items-center gap-3 px-4 sm:px-6 h-11 cursor-pointer"
      aria-label={`Updating to ${state.target_version}: ${PIP_PHASE_LABEL[state.phase]}`}
    >
      <RefreshCw size={15} className="animate-spin shrink-0" style={{ color: 'var(--accent)' }} aria-hidden />
      <span className="t-label" style={{ color: 'var(--fg)' }}>
        Updating to {state.target_version}
      </span>
      <span className="t-caption" style={{ color: 'var(--fg-muted)' }}>
        {PIP_PHASE_LABEL[state.phase]}
      </span>
      <span className="absolute left-0 right-0 bottom-0 h-[2px]" style={{ background: 'var(--hairline)' }}>
        <span
          className="block h-full transition-[width]"
          style={{ width: `${progress * 100}%`, background: 'var(--accent)' }}
        />
      </span>
    </div>
  );
}

function AvailableRow({
  status,
  busy,
  onPrimary,
  onSkip,
  onSnooze,
}: {
  status: UpdateStatus;
  busy: 'skip' | 'snooze' | null;
  onPrimary: () => void;
  onSkip: () => void;
  onSnooze: () => void;
}) {
  const primaryLabel = status.self_upgrade_supported ? 'Update' : 'How to update';
  return (
    <div className="flex items-center gap-3 px-4 sm:px-6 h-11 flex-wrap sm:flex-nowrap">
      <ArrowUpCircle size={16} className="shrink-0" style={{ color: 'var(--accent)' }} aria-hidden />
      <span className="t-body" style={{ color: 'var(--fg)' }}>
        <span style={{ fontWeight: 600 }}>UnifiOptimizer {status.latest_version}</span> is available
        , and you're on {status.current_version}.
      </span>
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
      <span className="flex-1" />
      <Button variant="primary" size="sm" onClick={onPrimary}>
        {primaryLabel}
      </Button>
      <Button variant="ghost" size="sm" onClick={onSkip} disabled={busy !== null}>
        {busy === 'skip' ? 'Skipping…' : 'Skip this version'}
      </Button>
      <button
        type="button"
        onClick={onSnooze}
        disabled={busy !== null}
        aria-label="Remind me in 7 days"
        title="Remind me in 7 days"
        className="inline-flex items-center justify-center w-7 h-7 rounded-control cursor-pointer transition-colors hover:bg-canvas shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
        style={{ color: 'var(--fg-subtle)' }}
      >
        <X size={15} />
      </button>
    </div>
  );
}
