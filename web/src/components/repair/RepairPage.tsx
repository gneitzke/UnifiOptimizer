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
  XCircle,
  RotateCcw,
  Loader2,
  ArrowRight,
  ArrowLeft,
  Shield,
  Eye,
  Play,
  History,
} from 'lucide-react';
import type {
  AnalysisResult,
  Finding,
  ChangePreview,
  ChangeResult,
  ChangeHistoryEntry,
} from '../../types/api';
import * as api from '../../services/api';

/* ── Action type metadata (matches RecsTab) ──── */

const ACTION_META: Record<string, { icon: string; label: string; color: string }> = {
  power_change: { icon: '⚡', label: 'TX Power', color: '#ff8c00' },
  channel_change: { icon: '📡', label: 'Channel', color: '#0088ff' },
  band_steering: { icon: '🔀', label: 'Band Steering', color: '#a855f7' },
  min_rssi: { icon: '📶', label: 'Min RSSI', color: '#00c48f' },
};

function getMeta(cat: string) {
  return ACTION_META[cat] ?? { icon: '🔧', label: cat.replace(/_/g, ' '), color: 'var(--primary)' };
}

/* ── Step pill bar ─────────────────────────────── */

const STEPS = ['Select', 'Preview', 'Confirm', 'Applying', 'Results'] as const;

function StepBar({ current }: { current: number }) {
  return (
    <div className="flex items-center gap-1">
      {STEPS.map((label, i) => {
        const active = i === current;
        const done = i < current;
        return (
          <div key={label} className="flex items-center gap-1">
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all"
              style={{
                background: active ? 'var(--primary)' : done ? 'rgba(0,136,255,0.1)' : 'var(--bg-elevated)',
                color: active ? '#fff' : done ? 'var(--primary)' : 'var(--text-muted)',
              }}>
              {done ? <CheckCircle2 size={12} /> : <span>{i + 1}</span>}
              <span className="hidden sm:inline">{label}</span>
            </div>
            {i < STEPS.length - 1 && (
              <div className="w-4 h-px" style={{ background: done ? 'var(--primary)' : 'var(--border)' }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ── Risk Badge ────────────────────────────────── */

function RiskBadge({ risk }: { risk: string }) {
  const map: Record<string, string> = { low: 'var(--success)', medium: 'var(--warning)', high: 'var(--error)' };
  const c = map[risk] ?? 'var(--text-muted)';
  return (
    <span className="text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full"
      style={{ background: `color-mix(in srgb, ${c} 15%, transparent)`, color: c }}>
      {risk}
    </span>
  );
}

/* ── Status Icon ───────────────────────────────── */

function StatusIcon({ status }: { status: 'pending' | 'running' | 'done' | 'err' }) {
  if (status === 'done') return <CheckCircle2 size={16} style={{ color: 'var(--success)' }} />;
  if (status === 'err') return <XCircle size={16} style={{ color: 'var(--error)' }} />;
  if (status === 'running') return <Loader2 size={16} className="animate-spin-slow" style={{ color: 'var(--primary)' }} />;
  return <div className="w-4 h-4 rounded-full" style={{ border: '2px solid var(--border)' }} />;
}

/* ── Confirmation Modal ────────────────────────── */

function ConfirmModal({
  title, message, confirmLabel, onConfirm, onCancel, dryRun, onDryRunChange,
}: {
  title: string; message: string; confirmLabel: string;
  onConfirm: () => void; onCancel: () => void;
  dryRun?: boolean; onDryRunChange?: (v: boolean) => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}>
      <motion.div initial={{ scale: 0.95, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }} className="glass-card-solid p-6 w-full max-w-md">
        <h3 className="text-base font-semibold mb-2" style={{ color: 'var(--text)' }}>{title}</h3>
        <p className="text-sm mb-4" style={{ color: 'var(--text-muted)' }}>{message}</p>
        {onDryRunChange !== undefined && (
          <label className="flex items-center gap-2 text-xs mb-4 cursor-pointer" style={{ color: 'var(--text-muted)' }}>
            <input type="checkbox" checked={dryRun} onChange={(e) => onDryRunChange(e.target.checked)}
              className="accent-[var(--primary)]" />
            Dry run (preview only, no changes applied)
          </label>
        )}
        <div className="flex gap-3 justify-end">
          <button onClick={onCancel} className="px-4 py-2 rounded-lg text-sm cursor-pointer"
            style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', border: 'none' }}>
            Cancel
          </button>
          <button onClick={onConfirm} className="px-4 py-2 rounded-lg text-sm font-medium cursor-pointer flex items-center gap-1.5"
            style={{ background: 'var(--primary)', color: '#fff', border: 'none' }}>
            <Play size={13} />
            {confirmLabel}
          </button>
        </div>
      </motion.div>
    </div>
  );
}

/* ── Helper: group findings by category ────────── */

function groupFindings(findings: Finding[]) {
  const grouped = new Map<string, Finding[]>();
  findings.forEach((f) => {
    const list = grouped.get(f.category) || [];
    list.push(f);
    grouped.set(f.category, list);
  });
  const order = Object.keys(ACTION_META);
  return [...grouped.entries()].sort(([a], [b]) => {
    const ai = order.indexOf(a);
    const bi = order.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });
}

/* ── Helper: group previews by category ────────── */

function groupPreviews(previews: ChangePreview[], findings: Finding[], _selectedIds: Set<string>) {
  const findingMap = new Map<string, Finding>();
  findings.forEach((f) => findingMap.set(f.id, f));

  const grouped = new Map<string, ChangePreview[]>();
  previews.forEach((p) => {
    // Match preview to finding by changeId → finding.id
    const finding = findingMap.get(p.changeId);
    const cat = finding?.category ?? 'other';
    const list = grouped.get(cat) || [];
    list.push(p);
    grouped.set(cat, list);
  });
  const order = Object.keys(ACTION_META);
  return [...grouped.entries()].sort(([a], [b]) => {
    const ai = order.indexOf(a);
    const bi = order.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });
}

/* ── Page Component ────────────────────────────── */

export default function RepairPage() {
  const navigate = useNavigate();

  const [step, setStep] = useState(0);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [previews, setPreviews] = useState<ChangePreview[]>([]);
  const [showConfirm, setShowConfirm] = useState(false);
  const [dryRun, setDryRun] = useState(false);
  const [results, setResults] = useState<Map<string, ChangeResult>>(new Map());
  const [applyStatus, setApplyStatus] = useState<Map<string, 'pending' | 'running' | 'done' | 'err'>>(new Map());
  const [history, setHistory] = useState<ChangeHistoryEntry[]>([]);
  const [revertTarget, setRevertTarget] = useState<ChangeHistoryEntry | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const jobId = params.get('jobId');
    if (!jobId) { setLoading(false); return; }
    api.getAnalysisResults(jobId)
      .then((r: AnalysisResult) => {
        setFindings(r.findings);
        const pre = params.get('findings');
        if (pre) setSelectedIds(new Set(pre.split(',')));
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  const loadHistory = useCallback(() => {
    api.getChangeHistory().then(setHistory).catch(() => {});
  }, []);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  function toggleFinding(id: string) {
    setSelectedIds((prev) => { const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next; });
  }

  function toggleGroup(cat: string) {
    const items = groupFindings(findings).find(([c]) => c === cat)?.[1] ?? [];
    setSelectedIds((prev) => {
      const next = new Set(prev);
      const allIn = items.every((f) => next.has(f.id));
      items.forEach((f) => (allIn ? next.delete(f.id) : next.add(f.id)));
      return next;
    });
  }

  async function fetchPreviews() {
    const params = new URLSearchParams(window.location.search);
    const jobId = params.get('jobId');
    if (!jobId) return;
    try {
      const { previews: p } = await api.previewRepair(jobId, [...selectedIds].map(Number));
      setPreviews(p);
      setStep(1);
    } catch (err) {
      console.error('Preview failed:', err);
    }
  }

  async function applyChanges() {
    setShowConfirm(false);
    setStep(3);
    const params = new URLSearchParams(window.location.search);
    const jobId = params.get('jobId');
    if (!jobId) return;
    const ids = [...selectedIds].map(Number);
    const statusMap = new Map<string, 'pending' | 'running' | 'done' | 'err'>();
    previews.forEach((p) => statusMap.set(p.changeId, 'pending'));
    setApplyStatus(new Map(statusMap));
    try {
      const res = await api.applyRepair(jobId, ids, dryRun);
      for (const r of res.results) {
        statusMap.set(r.changeId, r.success ? 'done' : 'err');
        setResults((prev) => new Map(prev).set(r.changeId, r));
      }
    } catch {
      previews.forEach((p) => statusMap.set(p.changeId, 'err'));
    }
    setApplyStatus(new Map(statusMap));
    setStep(4);
    loadHistory();
  }

  async function handleRevert(entry: ChangeHistoryEntry) {
    setRevertTarget(null);
    const idToRevert = (entry as any).realChangeId || entry.changeId;
    if (!idToRevert) return;
    try { await api.revertChange(idToRevert); loadHistory(); }
    catch (err) { console.error('Revert failed:', err); }
  }

  const variants = { enter: { opacity: 0, y: 12 }, center: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -12 } };

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto glass-card-solid p-12 text-center">
        <Loader2 size={28} className="mx-auto animate-spin-slow" style={{ color: 'var(--primary)' }} />
        <p className="text-xs mt-3" style={{ color: 'var(--text-muted)' }}>Loading analysis…</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="max-w-4xl mx-auto glass-card-solid p-12 text-center">
        <XCircle size={28} className="mx-auto mb-3" style={{ color: 'var(--error)' }} />
        <p className="text-sm" style={{ color: 'var(--error)' }}>{loadError}</p>
      </div>
    );
  }

  const sortedGroups = groupFindings(findings);

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      {/* Step indicator */}
      <div className="glass-card-solid px-5 py-3 flex items-center justify-between">
        <StepBar current={step} />
        {step > 0 && step < 3 && (
          <button onClick={() => setStep((s) => s - 1)}
            className="flex items-center gap-1 text-xs font-medium px-3 py-1.5 rounded-lg cursor-pointer"
            style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
            <ArrowLeft size={12} /> Back
          </button>
        )}
      </div>

      <AnimatePresence mode="wait">
        {/* ── Step 0: Select ───────────────── */}
        {step === 0 && (
          <motion.div key="select" variants={variants} initial="enter" animate="center" exit="exit"
            className="space-y-4">
            {findings.length === 0 ? (
              <div className="glass-card-solid p-12 text-center">
                <Shield size={32} className="mx-auto mb-3" style={{ color: 'var(--border-strong)' }} />
                <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
                  No recommendations available. Run an analysis first.
                </p>
                <button onClick={() => navigate('/analysis/new')}
                  className="mt-4 px-5 py-2 rounded-xl text-sm font-medium cursor-pointer"
                  style={{ background: 'var(--primary)', color: '#fff', border: 'none' }}>
                  Run Analysis
                </button>
              </div>
            ) : (
              <>
                {/* Summary + action bar */}
                <div className="sticky top-0 z-20 glass-card-solid p-3 flex items-center justify-between gap-3"
                  style={{ borderBottom: '2px solid var(--border)', backdropFilter: 'blur(12px)' }}>
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                      {findings.length} Changes Available
                    </span>
                    <div className="flex gap-1.5">
                      {sortedGroups.map(([cat, items]) => {
                        const meta = getMeta(cat);
                        return (
                          <span key={cat} className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                            style={{ background: `${meta.color}15`, color: meta.color }}>
                            {meta.icon} {items.length}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button onClick={() => {
                      if (selectedIds.size === findings.length) setSelectedIds(new Set());
                      else setSelectedIds(new Set(findings.map((f) => f.id)));
                    }}
                      className="text-xs font-medium px-3 py-1.5 rounded-lg cursor-pointer"
                      style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
                      {selectedIds.size === findings.length ? 'Deselect All' : 'Select All'}
                    </button>
                    {selectedIds.size > 0 && (
                      <button onClick={() => void fetchPreviews()}
                        className="text-xs font-semibold px-4 py-1.5 rounded-lg cursor-pointer flex items-center gap-1.5 shadow-md"
                        style={{ background: 'var(--primary)', color: '#fff', border: 'none' }}>
                        <Eye size={13} />
                        Preview {selectedIds.size} Change{selectedIds.size !== 1 ? 's' : ''}
                      </button>
                    )}
                  </div>
                </div>

                {/* Grouped findings — same layout as RecsTab */}
                {sortedGroups.map(([cat, items]) => {
                  const meta = getMeta(cat);
                  const groupSelected = items.filter((f) => selectedIds.has(f.id)).length;
                  const allGroupSelected = groupSelected === items.length;
                  return (
                    <div key={cat} className="glass-card-solid overflow-hidden">
                      <div className="px-5 py-3 flex items-center gap-3 cursor-pointer select-none"
                        onClick={() => toggleGroup(cat)}
                        style={{ borderBottom: '1px solid var(--border)' }}>
                        <input type="checkbox" checked={allGroupSelected} readOnly
                          className="shrink-0 accent-[var(--primary)] pointer-events-none"
                          {...(groupSelected > 0 && !allGroupSelected
                            ? { ref: (el: HTMLInputElement | null) => { if (el) el.indeterminate = true; } } : {})} />
                        <span className="text-base">{meta.icon}</span>
                        <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{meta.label}</span>
                        <span className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                          style={{ background: `${meta.color}15`, color: meta.color }}>
                          {items.length} {items.length === 1 ? 'change' : 'changes'}
                        </span>
                        {groupSelected > 0 && (
                          <span className="ml-auto text-[10px] font-medium px-2 py-0.5 rounded-full"
                            style={{ background: 'rgba(0,136,255,0.1)', color: 'var(--primary)' }}>
                            {groupSelected} selected
                          </span>
                        )}
                      </div>
                      <div>
                        {items.map((f, i) => {
                          const isSelected = selectedIds.has(f.id);
                          const deviceMatch = f.title.match(/^(.+?):\s*(.+)$/);
                          const deviceName = deviceMatch ? deviceMatch[1] : '';
                          const actionText = deviceMatch ? deviceMatch[2] : f.title;
                          return (
                            <label key={f.id}
                              className="flex items-center gap-4 px-5 py-3 cursor-pointer transition-colors"
                              style={{
                                background: isSelected ? `${meta.color}08` : 'transparent',
                                borderBottom: i < items.length - 1 ? '1px solid var(--border)' : 'none',
                              }}>
                              <input type="checkbox" checked={isSelected} onChange={() => toggleFinding(f.id)}
                                className="shrink-0 accent-[var(--primary)]" />
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                  {deviceName && (
                                    <span className="text-xs font-semibold" style={{ color: meta.color }}>{deviceName}</span>
                                  )}
                                  <span className="text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded"
                                    style={{
                                      background: f.severity === 'critical' ? 'rgba(239,68,68,0.1)' : f.severity === 'warning' ? 'rgba(245,158,11,0.1)' : 'rgba(0,196,143,0.1)',
                                      color: f.severity === 'critical' ? 'var(--error)' : f.severity === 'warning' ? 'var(--warning)' : 'var(--success)',
                                    }}>
                                    {f.severity}
                                  </span>
                                </div>
                                <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{f.description || actionText}</p>
                              </div>
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </>
            )}
          </motion.div>
        )}

        {/* ── Step 1: Preview ──────────────── */}
        {step === 1 && (
          <motion.div key="preview" variants={variants} initial="enter" animate="center" exit="exit"
            className="space-y-4">
            {/* Summary */}
            <div className="glass-card-solid p-4 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Eye size={18} style={{ color: 'var(--primary)' }} />
                <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                  {previews.length} Change{previews.length !== 1 ? 's' : ''} to Apply
                </span>
              </div>
              <button onClick={() => setShowConfirm(true)}
                className="text-xs font-semibold px-4 py-2 rounded-lg cursor-pointer flex items-center gap-1.5 shadow-md"
                style={{ background: 'var(--primary)', color: '#fff', border: 'none' }}>
                <Play size={13} />
                Apply Changes
              </button>
            </div>

            {/* Grouped preview cards */}
            {groupPreviews(previews, findings, selectedIds).map(([cat, items]) => {
              const meta = getMeta(cat);
              return (
                <div key={cat} className="glass-card-solid overflow-hidden">
                  <div className="px-5 py-3 flex items-center gap-3"
                    style={{ borderBottom: '1px solid var(--border)' }}>
                    <span className="text-base">{meta.icon}</span>
                    <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{meta.label}</span>
                    <span className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                      style={{ background: `${meta.color}15`, color: meta.color }}>
                      {items.length}
                    </span>
                  </div>
                  <div>
                    {items.map((p, i) => (
                      <div key={p.changeId} className="px-5 py-3 flex items-center gap-4"
                        style={{ borderBottom: i < items.length - 1 ? '1px solid var(--border)' : 'none' }}>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-xs font-semibold" style={{ color: meta.color }}>{p.deviceName}</span>
                            <RiskBadge risk={p.risk} />
                          </div>
                          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{p.description}</p>
                        </div>
                        <div className="shrink-0 flex items-center gap-2 font-mono text-xs">
                          <span className="px-2 py-1 rounded" style={{ background: 'rgba(239,68,68,0.08)', color: 'var(--error)' }}>
                            {p.setting}: {p.currentValue}
                          </span>
                          <ArrowRight size={12} style={{ color: 'var(--text-muted)' }} />
                          <span className="px-2 py-1 rounded" style={{ background: 'rgba(0,196,143,0.08)', color: 'var(--success)' }}>
                            {p.proposedValue}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </motion.div>
        )}

        {/* ── Step 3: Applying ─────────────── */}
        {step === 3 && (
          <motion.div key="applying" variants={variants} initial="enter" animate="center" exit="exit"
            className="space-y-3">
            <div className="glass-card-solid p-4 flex items-center gap-3">
              <Loader2 size={18} className="animate-spin-slow" style={{ color: 'var(--primary)' }} />
              <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                Applying {previews.length} change{previews.length !== 1 ? 's' : ''}…
              </span>
            </div>
            <div className="glass-card-solid overflow-hidden">
              {previews.map((p, i) => (
                <div key={p.changeId} className="px-5 py-3 flex items-center gap-3"
                  style={{ borderBottom: i < previews.length - 1 ? '1px solid var(--border)' : 'none' }}>
                  <StatusIcon status={applyStatus.get(p.changeId) ?? 'pending'} />
                  <div className="flex-1 min-w-0">
                    <span className="text-xs font-medium" style={{ color: 'var(--text)' }}>{p.description}</span>
                    <span className="text-[10px] ml-2" style={{ color: 'var(--text-muted)' }}>{p.deviceName}</span>
                  </div>
                </div>
              ))}
            </div>
          </motion.div>
        )}

        {/* ── Step 4: Results ──────────────── */}
        {step === 4 && (
          <motion.div key="results" variants={variants} initial="enter" animate="center" exit="exit"
            className="space-y-4">
            {/* Results summary */}
            {(() => {
              const success = previews.filter((p) => results.get(p.changeId)?.success).length;
              const failed = previews.length - success;
              return (
                <div className="glass-card-solid p-4 flex items-center gap-3">
                  {failed === 0
                    ? <CheckCircle2 size={20} style={{ color: 'var(--success)' }} />
                    : <XCircle size={20} style={{ color: 'var(--error)' }} />}
                  <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                    {success} succeeded{failed > 0 ? `, ${failed} failed` : ''}
                    {dryRun ? ' (dry run)' : ''}
                  </span>
                </div>
              );
            })()}

            <div className="glass-card-solid overflow-hidden">
              {previews.map((p, i) => {
                const r = results.get(p.changeId);
                return (
                  <div key={p.changeId} className="px-5 py-3 flex items-center gap-3"
                    style={{ borderBottom: i < previews.length - 1 ? '1px solid var(--border)' : 'none' }}>
                    <StatusIcon status={r?.success ? 'done' : 'err'} />
                    <div className="flex-1 min-w-0">
                      <span className="text-xs font-medium" style={{ color: 'var(--text)' }}>{p.description}</span>
                      <span className="text-[10px] ml-2" style={{ color: 'var(--text-muted)' }}>{p.deviceName}</span>
                      {r?.error && <p className="text-[10px] mt-0.5" style={{ color: 'var(--error)' }}>{r.error}</p>}
                    </div>
                    {r?.revertible && r.realChangeId && (
                      <button onClick={() => setRevertTarget({
                        changeId: r.realChangeId, description: p.description, deviceName: p.deviceName,
                        setting: p.setting, previousValue: p.currentValue, newValue: p.proposedValue,
                        appliedAt: r.appliedAt, appliedBy: '', reverted: false, device_mac: '',
                      } as ChangeHistoryEntry)}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-medium cursor-pointer"
                        style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
                        <RotateCcw size={10} /> Revert
                      </button>
                    )}
                  </div>
                );
              })}
            </div>

            <button onClick={() => { setStep(0); setResults(new Map()); setPreviews([]); setSelectedIds(new Set()); }}
              className="w-full py-2.5 rounded-xl text-sm font-medium cursor-pointer"
              style={{ background: 'var(--bg-elevated)', color: 'var(--text)', border: '1px solid var(--border)' }}>
              Done
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Change History ──────────────── */}
      {history.length > 0 && (
        <div className="glass-card-solid overflow-hidden">
          <div className="px-5 py-3 flex items-center gap-3" style={{ borderBottom: '1px solid var(--border)' }}>
            <History size={16} style={{ color: 'var(--text-muted)' }} />
            <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>Change History</span>
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full"
              style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>
              {history.length}
            </span>
          </div>
          <div>
            {history.map((h, i) => (
              <div key={h.changeId} className="px-5 py-2.5 flex items-center gap-3"
                style={{ borderBottom: i < history.length - 1 ? '1px solid var(--border)' : 'none' }}>
                {h.reverted
                  ? <RotateCcw size={13} style={{ color: 'var(--text-muted)' }} />
                  : <CheckCircle2 size={13} style={{ color: 'var(--success)' }} />}
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium truncate" style={{ color: 'var(--text)' }}>{h.description}</p>
                  <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                    {h.deviceName} · {new Date(h.appliedAt).toLocaleString()}{h.reverted ? ' · Reverted' : ''}
                  </p>
                </div>
                {!h.reverted && (
                  <button onClick={() => setRevertTarget(h)}
                    className="text-[10px] cursor-pointer flex items-center gap-1 px-2 py-1 rounded-lg"
                    style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
                    <RotateCcw size={10} /> Revert
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Modals ──────────────────────── */}
      <AnimatePresence>
        {showConfirm && (
          <ConfirmModal
            title="Apply Changes"
            message={`You are about to apply ${previews.length} change${previews.length > 1 ? 's' : ''}. This may cause brief connectivity disruptions.`}
            confirmLabel="Apply Changes"
            onConfirm={() => void applyChanges()}
            onCancel={() => setShowConfirm(false)}
            dryRun={dryRun} onDryRunChange={setDryRun}
          />
        )}
        {revertTarget && (
          <ConfirmModal
            title="Revert Change"
            message={`Restore ${revertTarget.deviceName} ${revertTarget.setting} to ${revertTarget.previousValue}?`}
            confirmLabel="Revert"
            onConfirm={() => void handleRevert(revertTarget)}
            onCancel={() => setRevertTarget(null)}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
