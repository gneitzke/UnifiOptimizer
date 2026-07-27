import { useState } from 'react';
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  RotateCcw,
  Wrench,
  X,
} from 'lucide-react';
import type { IssueState } from '../../api/types';
import { Button } from '../../components/ui/Button';
import { Skeleton } from '../../components/ui/Skeleton';
import { ApiError } from '../shared/api';
import {
  applyFix,
  getFixHistory,
  getFixPlan,
  revertFix,
  type FixChange,
  type FixHistoryResponse,
  type FixPlanResponse,
  type FixPlanStep,
  type FixVerification,
} from '../shared/api';
import { humanizeKey } from '../shared/format';
import { usePageAsync } from '../shared/hooks';

/**
 * Proposed fix (ARCHITECTURE.md §9, Gitea #26). Two independent reads:
 *
 * - **History** (`getFixHistory`, DB-only, never touches a device): whatever the
 *   ledger already knows for this issue — applied changes with their
 *   verification state and a Revert control. Loaded unconditionally, so a
 *   resolved issue shows what actually happened the instant the page opens,
 *   rather than gating real history behind a "preview" click.
 * - **Plan** (`getFixPlan`, a read-only device GET): the exact controller calls a
 *   fresh dry-run renders for a *new* remediation, behind a single guarded Apply
 *   button and a confirm modal. Only relevant while the issue is still open —
 *   there is nothing left to propose against a resolved one — and only fetched
 *   once the operator asks, never on page load; an apply happens only through
 *   the confirm modal here, the sole trigger besides the CLI.
 */

const RISK_TONE: Record<string, string> = {
  low: 'var(--fg-muted)',
  medium: 'var(--sev-p3)',
  high: 'var(--sev-p2)',
};

function RiskChip({ risk }: { risk: string }) {
  const tone = RISK_TONE[risk] ?? 'var(--fg-muted)';
  const fill =
    risk === 'low' || !RISK_TONE[risk]
      ? 'var(--canvas)'
      : `color-mix(in srgb, ${tone} 12%, transparent)`;
  return (
    <span
      className="inline-flex items-center h-[18px] px-1.5 rounded-full t-micro font-medium tnum"
      style={{ background: fill, color: tone }}
    >
      {risk} risk
    </span>
  );
}

function valueText(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'on' : 'off';
  return String(v);
}

function StepDiff({ step }: { step: FixPlanStep }) {
  const entries = Object.entries(step.diff ?? {});
  const changes = entries.filter(([, d]) => d.after !== null && d.after !== undefined);
  if (changes.length === 0) return null;
  return (
    <div className="flex flex-col gap-0.5 mt-1.5">
      {changes.map(([attr, d]) => (
        <div
          key={attr}
          className="grid font-mono items-baseline"
          style={{ gridTemplateColumns: 'minmax(90px,auto) 1fr', gap: 8, fontSize: 12 }}
        >
          <span style={{ color: 'var(--fg-muted)' }}>{humanizeKey(attr)}</span>
          <span>
            <span style={{ color: 'var(--fg-subtle)', textDecoration: 'line-through' }}>
              {valueText(d.before)}
            </span>
            <span style={{ color: 'var(--fg-subtle)' }}>{'  →  '}</span>
            <span style={{ color: 'var(--fg)' }}>{valueText(d.after)}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

function StepRow({ step, first }: { step: FixPlanStep; first: boolean }) {
  return (
    <div
      className="flex flex-col gap-1 py-3"
      style={{ borderTop: first ? 'none' : '1px solid var(--hairline)' }}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="t-body" style={{ color: 'var(--fg)' }}>
          {step.description}
        </span>
        <RiskChip risk={String(step.risk)} />
      </div>
      <code className="t-caption font-mono" style={{ color: 'var(--fg-muted)' }}>
        {step.method} {step.endpoint}
      </code>
      <StepDiff step={step} />
      {!step.revertible && (
        <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
          Transient command; not revertible from stored state.
        </span>
      )}
    </div>
  );
}

function VerificationBadge({ v }: { v: FixVerification }) {
  const map: Record<string, { text: string; tone: string; icon: React.ReactNode }> = {
    pending: {
      text: 'Watching the issue clear',
      tone: 'var(--fg-muted)',
      icon: <span className="inline-block w-2 h-2 rounded-full" style={{ background: 'var(--fg-subtle)' }} />,
    },
    verified: {
      text: 'Cleared and held',
      tone: 'var(--sev-healthy)',
      icon: <CheckCircle2 size={14} style={{ color: 'var(--sev-healthy)' }} />,
    },
    failed: {
      text: 'The issue re-fired after the fix',
      tone: 'var(--sev-p2)',
      icon: <X size={14} style={{ color: 'var(--sev-p2)' }} />,
    },
    expired: {
      text: 'Window lapsed without a clean resolve',
      tone: 'var(--fg-muted)',
      icon: <span className="inline-block w-2 h-2 rounded-full" style={{ background: 'var(--fg-subtle)' }} />,
    },
  };
  const entry = map[v.status];
  if (!entry) return null;
  return (
    <span className="inline-flex items-center gap-1.5 t-caption" style={{ color: entry.tone }}>
      {entry.icon}
      {entry.text}
    </span>
  );
}

function AppliedChange({
  change,
  verification,
  onRevert,
  busy,
}: {
  change: FixChange;
  verification: FixVerification;
  onRevert: (id: number) => void;
  busy: boolean;
}) {
  const reverted = change.status === 'reverted';
  return (
    <div
      className="flex items-center justify-between gap-3 py-2.5"
      style={{ borderTop: '1px solid var(--hairline)' }}
    >
      <div className="flex flex-col gap-0.5 min-w-0">
        <div className="flex items-center gap-2">
          <Check size={14} style={{ color: reverted ? 'var(--fg-subtle)' : 'var(--sev-healthy)' }} />
          <span className="t-body" style={{ color: 'var(--fg)' }}>
            {humanizeKey(change.action)}
          </span>
          <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
            {reverted ? 'reverted' : 'applied'}
          </span>
        </div>
        {/* Which device this row touched. A joint band re-plan ledgers one change
            per radio moved, so without the name every card reads identically and
            the operator cannot tell which AP a Revert button belongs to. */}
        {(change.entity_name || change.entity_native_id) && (
          <span className="t-micro truncate" style={{ color: 'var(--fg-muted)' }}>
            {change.entity_name || change.entity_native_id}
          </span>
        )}
        {!reverted && <VerificationBadge v={verification} />}
      </div>
      {!reverted && change.revertible && (
        <Button variant="secondary" size="sm" disabled={busy} onClick={() => onRevert(change.id)}>
          <RotateCcw size={13} />
          Revert
        </Button>
      )}
    </div>
  );
}

function ConfirmModal({
  plan,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  plan: FixPlanResponse;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'color-mix(in srgb, black 45%, transparent)' }}
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
      aria-label="Confirm apply fix"
    >
      <div
        className="w-full rounded-card flex flex-col gap-4 p-5"
        style={{
          maxWidth: 520,
          background: 'var(--elevated)',
          border: '1px solid var(--hairline)',
          boxShadow: 'var(--shadow-elevated)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-2.5">
          <AlertTriangle size={18} style={{ color: 'var(--sev-p2)', marginTop: 2, flexShrink: 0 }} />
          <div>
            <h3 className="t-section" style={{ color: 'var(--fg)' }}>
              Apply this fix to the controller?
            </h3>
            <p className="t-secondary mt-0.5" style={{ color: 'var(--fg-muted)' }}>
              This sends the change now. The current state is captured first, so you
              can revert it from the change ledger.
            </p>
          </div>
        </div>

        <div
          className="rounded-control p-3 flex flex-col"
          style={{ background: 'var(--canvas)', border: '1px solid var(--hairline)' }}
        >
          {plan.steps.map((s, i) => (
            <StepRow key={`${s.target}-${i}`} step={s} first={i === 0} />
          ))}
        </div>

        {error && (
          <span className="t-caption" style={{ color: 'var(--sev-p1)' }}>
            {error}
          </span>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" disabled={busy} onClick={onConfirm}>
            <Wrench size={13} />
            {busy ? 'Applying…' : 'Apply fix now'}
          </Button>
        </div>
      </div>
    </div>
  );
}

function applyErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 409)
      return 'The device changed since you opened this plan. Reload and review the new plan before applying.';
    if (e.status === 400) return 'The apply was refused by a safety rail. Nothing was sent.';
    if (e.status === 503) return 'The controller is not configured, so nothing can be applied.';
    return e.message;
  }
  return (e as Error).message || 'Apply failed';
}

/** A resolved issue with no ledger entry never went through the fix engine at
 * all — it cleared on its own (a flaky upstream, a transient condition) or its
 * only remediation is physical. Either way there is nothing to show but this. */
function NoFixApplied() {
  return (
    <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
      This issue resolved without an automatic fix being applied.
    </p>
  );
}

function AppliedChangesList({
  changes,
  verification,
  onRevert,
  busy,
  bordered,
}: {
  changes: FixChange[];
  verification: FixVerification;
  onRevert: (id: number) => void;
  busy: boolean;
  bordered: boolean;
}) {
  if (changes.length === 0) return null;
  return (
    <div
      className="flex flex-col pt-1"
      style={bordered ? { borderTop: '1px solid var(--hairline)' } : undefined}
    >
      <span className="t-micro mt-2 mb-0.5" style={{ color: 'var(--fg-subtle)' }}>
        Applied changes
      </span>
      {changes.map((c) => (
        <AppliedChange key={c.id} change={c} verification={verification} onRevert={onRevert} busy={busy} />
      ))}
    </div>
  );
}

/** The live preview -> confirm -> apply flow for a *new* remediation. Only
 * meaningful while the issue is still open; a resolved issue has nothing left
 * to propose. */
function FixPlanPreview({
  issueId,
  onApplied,
}: {
  issueId: number;
  onApplied: () => void;
}) {
  const [armed, setArmed] = useState(false);
  // The fetch is a read-only device GET, so it fires only once the operator asks
  // (armed): until then the fn resolves undefined and reaches no controller.
  const { data: plan, error, loading, reload } = usePageAsync<FixPlanResponse | undefined>(
    () => (armed ? getFixPlan(issueId) : Promise.resolve(undefined)),
    [issueId, armed],
  );
  const [modalOpen, setModalOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);

  if (!armed) {
    return (
      <div className="flex flex-col gap-3">
        <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
          Preview the exact change this fix would make on the controller. This
          only reads the current device configuration; nothing is sent until you
          apply it.
        </p>
        <Button variant="secondary" size="sm" className="self-start" onClick={() => setArmed(true)}>
          <Wrench size={14} />
          Preview fix plan
        </Button>
      </div>
    );
  }

  if (loading && !plan) {
    return <Skeleton className="h-24 w-full" />;
  }

  if (error) {
    const msg =
      error.status === 503
        ? 'The controller is not configured, so fixes can’t be planned or applied. Set it up in Settings.'
        : error.status === 422
          ? 'This issue has no automatically fixable target.'
          : error.status === 404
            ? 'This issue no longer exists.'
            : 'The fix engine is unreachable right now.';
    return (
      <div className="flex flex-col gap-2">
        <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
          {msg}
        </p>
        <button
          type="button"
          className="t-caption self-start cursor-pointer hover:underline"
          style={{ color: 'var(--accent)' }}
          onClick={reload}
        >
          Try again
        </button>
      </div>
    );
  }

  if (!plan) return null;

  const actionable = !plan.manual_action_required && plan.steps.length > 0;

  async function doApply() {
    if (!plan) return;
    setBusy(true);
    setApplyError(null);
    try {
      await applyFix(issueId, plan.confirm_token);
      setModalOpen(false);
      reload();
      onApplied();
    } catch (e) {
      setApplyError(applyErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {plan.manual_action_required ? (
        <div
          className="rounded-control p-3 flex items-start gap-2.5"
          style={{ background: 'var(--canvas)', border: '1px solid var(--hairline)' }}
        >
          <AlertTriangle size={16} style={{ color: 'var(--sev-p3)', marginTop: 2, flexShrink: 0 }} />
          <div>
            <div className="t-label" style={{ color: 'var(--fg)' }}>
              Manual action required
            </div>
            <p className="t-secondary mt-0.5" style={{ color: 'var(--fg-muted)' }}>
              {plan.advisory ?? 'No safe automatic fix. Remediate on site.'}
            </p>
          </div>
        </div>
      ) : actionable ? (
        <>
          <div className="flex flex-col">
            {plan.steps.map((s, i) => (
              <StepRow key={`${s.target}-${i}`} step={s} first={i === 0} />
            ))}
          </div>
          <div className="flex items-center justify-between gap-3 pt-1">
            <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
              {plan.device_count} device{plan.device_count === 1 ? '' : 's'} · reviewed
              before it sends
            </span>
            <Button variant="primary" size="sm" disabled={busy} onClick={() => setModalOpen(true)}>
              <Wrench size={13} />
              Apply fix
            </Button>
          </div>
        </>
      ) : (
        <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
          No automatic remediation is proposed for this issue.
        </p>
      )}

      {modalOpen && (
        <ConfirmModal
          plan={plan}
          busy={busy}
          error={applyError}
          onCancel={() => {
            setModalOpen(false);
            setApplyError(null);
          }}
          onConfirm={doApply}
        />
      )}
    </div>
  );
}

export function ProposedFix({
  issueId,
  issueState,
  onChanged,
}: {
  issueId: number;
  issueState: IssueState;
  onChanged: () => void;
}) {
  const isOpen = issueState !== 'resolved';
  // Store-only: safe to fetch unconditionally, on every issue-detail load,
  // resolved or not — it never reaches a device (see getFixHistory).
  const { data: history, error: historyError, loading: historyLoading, reload: reloadHistory } =
    usePageAsync<FixHistoryResponse>(() => getFixHistory(issueId), [issueId]);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  if (historyLoading && !history) {
    return <Skeleton className="h-24 w-full" />;
  }

  if (historyError || !history) {
    return (
      <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
        {historyError?.status === 404
          ? 'This issue no longer exists.'
          : 'Could not load this issue’s fix history.'}
      </p>
    );
  }

  const appliedChanges = history.changes;

  async function doRevert(changeId: number) {
    setBusy(true);
    setActionError(null);
    try {
      await revertFix(issueId, changeId);
      reloadHistory();
      onChanged();
    } catch (e) {
      setActionError((e as Error).message || 'Revert failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {isOpen ? (
        <FixPlanPreview
          issueId={issueId}
          onApplied={() => {
            reloadHistory();
            onChanged();
          }}
        />
      ) : (
        appliedChanges.length === 0 && <NoFixApplied />
      )}

      <AppliedChangesList
        changes={appliedChanges}
        verification={history.verification}
        onRevert={doRevert}
        busy={busy}
        bordered={isOpen}
      />

      {actionError && (
        <span className="t-caption" style={{ color: 'var(--sev-p1)' }}>
          {actionError}
        </span>
      )}
    </div>
  );
}
