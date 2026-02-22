import {
  useState,
  useEffect,
  useCallback,
} from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AnimatePresence,
  motion,
} from 'framer-motion';
import {
  CheckCircle2,
  Clock,
  XCircle,
  RotateCcw,
  Loader2,
  ArrowRight,
  Shield,
} from 'lucide-react';
import type {
  AnalysisResult,
  Finding,
  ChangePreview,
  ChangeResult,
  ChangeHistoryEntry,
} from '../../types/api';
import * as api from '../../services/api';

/* ── Step indicator ────────────────────────────── */

const STEPS = [
  'Select',
  'Preview',
  'Confirm',
  'Applying',
  'Results',
] as const;

function StepBar({
  current,
}: {
  current: number;
}) {
  return (
    <div className="flex items-center gap-2 mb-6">
      {STEPS.map((label, i) => (
        <div
          key={label}
          className="flex items-center gap-2"
        >
          <div
            className="w-7 h-7 rounded-full flex
              items-center justify-center
              text-xs font-bold"
            style={{
              background:
                i <= current
                  ? 'var(--primary)'
                  : 'var(--bg-elevated)',
              color:
                i <= current
                  ? '#fff'
                  : 'var(--text-muted)',
            }}
          >
            {i + 1}
          </div>
          <span
            className="text-xs hidden sm:inline"
            style={{
              color:
                i <= current
                  ? 'var(--text)'
                  : 'var(--text-muted)',
            }}
          >
            {label}
          </span>
          {i < STEPS.length - 1 && (
            <div
              className="w-6 h-px"
              style={{
                background: 'var(--border)',
              }}
            />
          )}
        </div>
      ))}
    </div>
  );
}

/* ── Risk Badge ────────────────────────────────── */

function RiskBadge({
  risk,
}: {
  risk: string;
}) {
  const map: Record<string, string> = {
    low: 'var(--success)',
    medium: 'var(--warning)',
    high: 'var(--error)',
  };
  const c = map[risk] ?? 'var(--text-muted)';
  return (
    <span
      className="text-[10px] font-semibold
        uppercase px-2 py-0.5 rounded-full"
      style={{
        background: `color-mix(
          in srgb, ${c} 20%, transparent
        )`,
        color: c,
      }}
    >
      {risk}
    </span>
  );
}

/* ── Status Icon ───────────────────────────────── */

function StatusIcon({
  status,
}: {
  status: 'pending' | 'running' | 'done' | 'err';
}) {
  if (status === 'done')
    return (
      <CheckCircle2
        size={18}
        style={{ color: 'var(--success)' }}
      />
    );
  if (status === 'err')
    return (
      <XCircle
        size={18}
        style={{ color: 'var(--error)' }}
      />
    );
  if (status === 'running')
    return (
      <Loader2
        size={18}
        className="animate-spin-slow"
        style={{ color: 'var(--primary)' }}
      />
    );
  return (
    <Clock
      size={18}
      style={{ color: 'var(--text-muted)' }}
    />
  );
}

/* ── Confirmation Modal ────────────────────────── */

function ConfirmModal({
  title,
  message,
  confirmLabel,
  onConfirm,
  onCancel,
  dryRun,
  onDryRunChange,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  dryRun?: boolean;
  onDryRunChange?: (v: boolean) => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex
        items-center justify-center p-4"
      style={{
        background: 'rgba(0,0,0,0.6)',
      }}
    >
      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        className="glass-card-solid p-6 w-full
          max-w-md"
      >
        <h3
          className="text-base font-semibold mb-2"
          style={{ color: 'var(--text)' }}
        >
          {title}
        </h3>
        <p
          className="text-sm mb-4"
          style={{ color: 'var(--text-muted)' }}
        >
          {message}
        </p>

        {onDryRunChange !== undefined && (
          <label
            className="flex items-center gap-2
              text-xs mb-4 cursor-pointer"
            style={{
              color: 'var(--text-muted)',
            }}
          >
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) =>
                onDryRunChange(e.target.checked)
              }
              className="accent-[var(--primary)]"
            />
            Dry run (preview only)
          </label>
        )}

        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg
              text-sm cursor-pointer"
            style={{
              background: 'var(--bg-elevated)',
              color: 'var(--text-muted)',
              border: 'none',
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 rounded-lg
              text-sm font-medium cursor-pointer"
            style={{
              background: 'var(--primary)',
              color: '#fff',
              border: 'none',
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </motion.div>
    </div>
  );
}

/* ── Page Component ────────────────────────────── */

export default function RepairPage() {
  const navigate = useNavigate();

  const [step, setStep] = useState(0);
  const [findings, setFindings] = useState<
    Finding[]
  >([]);
  const [selectedIds, setSelectedIds] = useState<
    Set<string>
  >(new Set());
  const [previews, setPreviews] = useState<
    ChangePreview[]
  >([]);
  const [showConfirm, setShowConfirm] =
    useState(false);
  const [dryRun, setDryRun] = useState(false);
  const [results, setResults] = useState<
    Map<string, ChangeResult>
  >(new Map());
  const [applyStatus, setApplyStatus] = useState<
    Map<
      string,
      'pending' | 'running' | 'done' | 'err'
    >
  >(new Map());
  const [history, setHistory] = useState<
    ChangeHistoryEntry[]
  >([]);
  const [revertTarget, setRevertTarget] =
    useState<ChangeHistoryEntry | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  /* Load latest analysis findings */
  useEffect(() => {
    const params = new URLSearchParams(
      window.location.search,
    );
    const jobId = params.get('jobId');
    if (!jobId) {
      setLoading(false);
      return;
    }
    api
      .getAnalysisResults(jobId)
      .then((r: AnalysisResult) => {
        setFindings(r.findings);
        const pre = params.get('findings');
        if (pre) {
          setSelectedIds(
            new Set(pre.split(',')),
          );
        }
      })
      .catch((err) => {
        setLoadError(
          err instanceof Error
            ? err.message
            : 'Failed to load analysis results',
        );
      })
      .finally(() => setLoading(false));
  }, []);

  /* Load change history */
  const loadHistory = useCallback(() => {
    api
      .getChangeHistory()
      .then(setHistory)
      .catch((err) => {
        setLoadError(
          err instanceof Error
            ? err.message
            : 'Failed to load change history',
        );
      });
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  /* Toggle finding selection */
  function toggleFinding(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  /* Fetch previews */
  async function fetchPreviews() {
    const params = new URLSearchParams(
      window.location.search,
    );
    const jobId = params.get('jobId');
    if (!jobId) return;
    const ids = [...selectedIds].map(Number);
    const { previews: p } =
      await api.previewRepair(jobId, ids);
    setPreviews(p);
    setStep(1);
  }

  /* Apply changes */
  async function applyChanges() {
    setShowConfirm(false);
    setStep(3);

    const params = new URLSearchParams(
      window.location.search,
    );
    const jobId = params.get('jobId');
    if (!jobId) return;

    const ids = [...selectedIds].map(Number);

    const statusMap = new Map<
      string,
      'pending' | 'running' | 'done' | 'err'
    >();
    previews.forEach((p) =>
      statusMap.set(p.changeId, 'pending'),
    );
    setApplyStatus(new Map(statusMap));

    try {
      const res = await api.applyRepair(
        jobId,
        ids,
        dryRun,
      );
      for (const r of res.results) {
        // Use changeId (mapped from recommendation_index) to update the status map
        // This matches the key used in previews.forEach above
        statusMap.set(r.changeId, r.success ? 'done' : 'err');
        setResults((prev) =>
          new Map(prev).set(r.changeId, r),
        );
      }
    } catch {
      previews.forEach((p) =>
        statusMap.set(p.changeId, 'err'),
      );
    }
    setApplyStatus(new Map(statusMap));

    setStep(4);
    loadHistory();
  }

  /* Revert a single change */
  async function handleRevert(
    entry: ChangeHistoryEntry,
  ) {
    setRevertTarget(null);
    // Use realChangeId if available (for recent applies), otherwise changeId (from history)
    // The history API returns change_id which is mapped to changeId in mapChangeEntry
    const idToRevert = (entry as any).realChangeId || entry.changeId;
    if (!idToRevert) {
       console.error("Missing change ID for revert");
       return;
    }
    await api.revertChange(idToRevert);
    loadHistory();
  }

  /* ── Transition wrapper ───────────────── */
  const variants = {
    enter: {
      opacity: 0,
      x: 20,
    },
    center: {
      opacity: 1,
      x: 0,
    },
    exit: {
      opacity: 0,
      x: -20,
    },
  };

  if (loading) {
    return (
      <div
        className="max-w-4xl mx-auto
          glass-card-solid p-8 text-center"
      >
        <Loader2
          size={32}
          className="mx-auto animate-spin-slow"
          style={{ color: 'var(--primary)' }}
        />
      </div>
    );
  }

  if (loadError) {
    return (
      <div
        className="max-w-4xl mx-auto
          glass-card-solid p-8 text-center"
      >
        <XCircle
          size={32}
          className="mx-auto mb-3"
          style={{ color: 'var(--error)' }}
        />
        <p
          className="text-sm"
          style={{ color: 'var(--error)' }}
        >
          {loadError}
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <StepBar current={step} />

      <AnimatePresence mode="wait">
        {/* ── Step 1: Select ─────────────── */}
        {step === 0 && (
          <motion.div
            key="select"
            variants={variants}
            initial="enter"
            animate="center"
            exit="exit"
            className="space-y-3"
          >
            {findings.length === 0 ? (
              <div
                className="glass-card-solid p-8
                  text-center"
              >
                <Shield
                  size={32}
                  className="mx-auto mb-2"
                  style={{
                    color:
                      'var(--border-strong)',
                  }}
                />
                <p
                  style={{
                    color: 'var(--text-muted)',
                  }}
                  className="text-sm"
                >
                  No recommendations available.
                  Run an analysis first.
                </p>
                <button
                  onClick={() =>
                    navigate('/analysis/new')
                  }
                  className="mt-4 px-5 py-2
                    rounded-xl text-sm
                    font-medium cursor-pointer"
                  style={{
                    background: 'var(--primary)',
                    color: '#fff',
                    border: 'none',
                  }}
                >
                  Run Analysis
                </button>
              </div>
            ) : (
              <>
                {findings.map((f) => (
                  <label
                    key={f.id}
                    className="glass-card-solid
                      p-4 flex items-start gap-3
                      cursor-pointer
                      hover:opacity-90
                      transition-opacity"
                  >
                    <input
                      type="checkbox"
                      checked={selectedIds.has(
                        f.id,
                      )}
                      onChange={() =>
                        toggleFinding(f.id)
                      }
                      className="mt-1
                        accent-[var(--primary)]"
                    />
                    <div className="flex-1">
                      <h4
                        className="text-sm
                          font-medium"
                        style={{
                          color: 'var(--text)',
                        }}
                      >
                        {f.title}
                      </h4>
                      <p
                        className="text-xs mt-1"
                        style={{
                          color:
                            'var(--text-muted)',
                        }}
                      >
                        {f.description}
                      </p>
                    </div>
                  </label>
                ))}
                {selectedIds.size > 0 && (
                  <button
                    onClick={() =>
                      void fetchPreviews()
                    }
                    className="w-full py-3
                      rounded-xl text-sm
                      font-medium cursor-pointer"
                    style={{
                      background:
                        'var(--primary)',
                      color: '#fff',
                      border: 'none',
                    }}
                  >
                    Preview Changes
                    <ArrowRight
                      size={14}
                      className="inline ml-2
                        -mt-0.5"
                    />
                  </button>
                )}
              </>
            )}
          </motion.div>
        )}

        {/* ── Step 2: Preview ────────────── */}
        {step === 1 && (
          <motion.div
            key="preview"
            variants={variants}
            initial="enter"
            animate="center"
            exit="exit"
            className="space-y-3"
          >
            {previews.map((p) => (
              <div
                key={p.changeId}
                className="glass-card-solid p-5"
              >
                <div
                  className="flex items-center
                    justify-between mb-2"
                >
                  <span
                    className="text-sm
                      font-medium"
                    style={{
                      color: 'var(--text)',
                    }}
                  >
                    {p.deviceName}
                  </span>
                  <RiskBadge risk={p.risk} />
                </div>
                <p
                  className="text-xs mb-2"
                  style={{
                    color: 'var(--text-muted)',
                  }}
                >
                  {p.description}
                </p>
                <div
                  className="flex items-center
                    gap-2 text-xs font-mono"
                >
                  <span
                    style={{
                      color: 'var(--error)',
                    }}
                  >
                    {p.setting}:
                    {p.currentValue}
                  </span>
                  <ArrowRight
                    size={12}
                    style={{
                      color:
                        'var(--text-muted)',
                    }}
                  />
                  <span
                    style={{
                      color: 'var(--success)',
                    }}
                  >
                    {p.proposedValue}
                  </span>
                </div>
              </div>
            ))}
            <div className="flex gap-3">
              <button
                onClick={() => setStep(0)}
                className="flex-1 py-3 rounded-xl
                  text-sm cursor-pointer"
                style={{
                  background:
                    'var(--bg-elevated)',
                  color: 'var(--text-muted)',
                  border: 'none',
                }}
              >
                Back
              </button>
              <button
                onClick={() =>
                  setShowConfirm(true)
                }
                className="flex-1 py-3 rounded-xl
                  text-sm font-medium
                  cursor-pointer"
                style={{
                  background: 'var(--primary)',
                  color: '#fff',
                  border: 'none',
                }}
              >
                Apply Changes
              </button>
            </div>
          </motion.div>
        )}

        {/* ── Step 3: Applying ───────────── */}
        {step === 3 && (
          <motion.div
            key="applying"
            variants={variants}
            initial="enter"
            animate="center"
            exit="exit"
            className="space-y-3"
          >
            {previews.map((p) => (
              <div
                key={p.changeId}
                className="glass-card-solid p-4
                  flex items-center gap-3"
              >
                <StatusIcon
                  status={
                    applyStatus.get(
                      p.changeId,
                    ) ?? 'pending'
                  }
                />
                <div className="flex-1">
                  <p
                    className="text-sm"
                    style={{
                      color: 'var(--text)',
                    }}
                  >
                    {p.description}
                  </p>
                  <p
                    className="text-xs"
                    style={{
                      color:
                        'var(--text-muted)',
                    }}
                  >
                    {p.deviceName}
                  </p>
                </div>
              </div>
            ))}
          </motion.div>
        )}

        {/* ── Step 4: Results ────────────── */}
        {step === 4 && (
          <motion.div
            key="results"
            variants={variants}
            initial="enter"
            animate="center"
            exit="exit"
            className="space-y-3"
          >
            {previews.map((p) => {
              const r = results.get(p.changeId);
              return (
                <div
                  key={p.changeId}
                  className="glass-card-solid p-4
                    flex items-center gap-3"
                >
                  <StatusIcon
                    status={
                      r?.success
                        ? 'done'
                        : 'err'
                    }
                  />
                  <div className="flex-1">
                    <p
                      className="text-sm"
                      style={{
                        color: 'var(--text)',
                      }}
                    >
                      {p.description}
                    </p>
                    {r?.error && (
                      <p
                        className="text-xs"
                        style={{
                          color: 'var(--error)',
                        }}
                      >
                        {r.error}
                      </p>
                    )}
                  </div>
                  {r?.revertible && r.realChangeId && (
                    <button
                      onClick={() => {
                        setRevertTarget({
                          changeId: r.realChangeId,
                          description:
                            p.description,
                          deviceName:
                            p.deviceName,
                          setting: p.setting,
                          previousValue:
                            p.currentValue,
                          newValue:
                            p.proposedValue,
                          appliedAt:
                            r.appliedAt,
                          appliedBy: '',
                          reverted: false,
                          device_mac: '', // Preview doesn't return mac, but it's not used for revert call here
                        } as ChangeHistoryEntry);
                      }}
                      className="flex items-center
                        gap-1 px-3 py-1.5
                        rounded-lg text-xs
                        font-medium cursor-pointer"
                      style={{
                        background:
                          'var(--bg-elevated)',
                        color:
                          'var(--text-muted)',
                        border: 'none',
                      }}
                    >
                      <RotateCcw size={12} />
                      Revert
                    </button>
                  )}
                </div>
              );
            })}
            <button
              onClick={() => {
                setStep(0);
                setResults(new Map());
                setPreviews([]);
                setSelectedIds(new Set());
              }}
              className="w-full py-3 rounded-xl
                text-sm font-medium
                cursor-pointer"
              style={{
                background:
                  'var(--bg-elevated)',
                color: 'var(--text)',
                border: 'none',
              }}
            >
              Done
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Change History ─────────────── */}
      {history.length > 0 && (
        <div className="glass-card-solid p-6">
          <h3
            className="text-sm font-semibold mb-4"
            style={{ color: 'var(--text)' }}
          >
            Change History
          </h3>
          <div className="space-y-3">
            {history.map((h) => (
              <div
                key={h.changeId}
                className="flex items-center
                  gap-3 py-2 px-3 rounded-lg"
                style={{
                  background:
                    'var(--bg-elevated)',
                }}
              >
                {h.reverted ? (
                  <RotateCcw
                    size={14}
                    style={{
                      color:
                        'var(--text-muted)',
                    }}
                  />
                ) : (
                  <CheckCircle2
                    size={14}
                    style={{
                      color: 'var(--success)',
                    }}
                  />
                )}
                <div className="flex-1 min-w-0">
                  <p
                    className="text-xs
                      font-medium truncate"
                    style={{
                      color: 'var(--text)',
                    }}
                  >
                    {h.description}
                  </p>
                  <p
                    className="text-[10px]"
                    style={{
                      color:
                        'var(--text-muted)',
                    }}
                  >
                    {h.deviceName} ·{' '}
                    {new Date(
                      h.appliedAt,
                    ).toLocaleString()}
                    {h.reverted &&
                      ' · Reverted'}
                  </p>
                </div>
                {!h.reverted && (
                  <button
                    onClick={() =>
                      setRevertTarget(h)
                    }
                    className="text-xs
                      cursor-pointer"
                    style={{
                      color:
                        'var(--text-muted)',
                      background: 'transparent',
                      border: 'none',
                    }}
                  >
                    <RotateCcw size={12} />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Modals ─────────────────────── */}
      <AnimatePresence>
        {showConfirm && (
          <ConfirmModal
            title="Apply Changes"
            message={`You are about to apply ${
              previews.length
            } change${
              previews.length > 1 ? 's' : ''
            }. This may cause brief
            connectivity disruptions.`}
            confirmLabel="Apply Changes"
            onConfirm={() =>
              void applyChanges()
            }
            onCancel={() =>
              setShowConfirm(false)
            }
            dryRun={dryRun}
            onDryRunChange={setDryRun}
          />
        )}

        {revertTarget && (
          <ConfirmModal
            title="Revert Change"
            message={`Restore ${
              revertTarget.deviceName
            } ${revertTarget.setting} to ${
              revertTarget.previousValue
            }?`}
            confirmLabel="Revert"
            onConfirm={() =>
              void handleRevert(revertTarget)
            }
            onCancel={() =>
              setRevertTarget(null)
            }
          />
        )}
      </AnimatePresence>
    </div>
  );
}
