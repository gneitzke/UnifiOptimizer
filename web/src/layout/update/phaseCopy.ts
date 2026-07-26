import type { UpgradePhase } from '../../api/update';

/**
 * Human copy for the runner's state machine (`netadmin/upgrade/journal.py`).
 * Shared between the pip progress view and anywhere else a phase needs a label.
 */

export const PIP_PHASE_ORDER: readonly UpgradePhase[] = [
  'starting',
  'preflight',
  'downloading',
  'staging',
  'smoke_testing',
  'backing_up',
  'swapping',
  'restarting',
  'verifying',
];

export const PIP_PHASE_LABEL: Record<UpgradePhase, string> = {
  starting: 'Starting…',
  preflight: 'Checking free disk space…',
  downloading: 'Downloading the update…',
  staging: 'Installing into a new environment…',
  smoke_testing: 'Testing the new version against a copy of your data…',
  backing_up: 'Backing up the database…',
  swapping: 'Switching over…',
  restarting: 'Restarting…',
  verifying: 'Verifying the new version is healthy…',
  done: 'Update complete.',
  rolled_back: 'Rolled back to the previous version.',
  failed: 'Update failed.',
};

/** 0..1 fill for a slim progress bar; terminal phases read as complete. */
export function phaseProgress(phase: UpgradePhase): number {
  const i = PIP_PHASE_ORDER.indexOf(phase);
  if (i === -1) return 1; // done | rolled_back | failed
  return (i + 1) / PIP_PHASE_ORDER.length;
}
