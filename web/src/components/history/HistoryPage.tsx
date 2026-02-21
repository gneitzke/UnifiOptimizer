import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Trash2,
  GitCompare,
  Radio,
  Users,
  Activity,
} from 'lucide-react';
import type { AnalysisRecord } from
  '../../services/db';
import {
  getHistory,
  deleteAnalysis,
} from '../../services/db';
import HealthRing from
  '../dashboard/HealthRing';

/* ── Score Badge ───────────────────────────────── */

function ScoreBadge({
  score,
}: {
  score: number;
}) {
  const color =
    score > 80
      ? 'var(--success)'
      : score > 60
        ? 'var(--warning)'
        : 'var(--error)';
  return (
    <span
      className="text-xs font-bold px-2 py-0.5
        rounded-full"
      style={{
        background: `color-mix(
          in srgb, ${color} 20%, transparent
        )`,
        color,
      }}
    >
      {score}
    </span>
  );
}

/* ── Compare View ──────────────────────────────── */

function CompareView({
  a,
  b,
  onClose,
}: {
  a: AnalysisRecord;
  b: AnalysisRecord;
  onClose: () => void;
}) {
  const diff = b.healthScore - a.healthScore;
  const diffColor =
    diff > 0
      ? 'var(--success)'
      : diff < 0
        ? 'var(--error)'
        : 'var(--text-muted)';

  return (
    <div className="glass-card-solid p-6">
      <div
        className="flex items-center
          justify-between mb-4"
      >
        <h3
          className="text-sm font-semibold"
          style={{ color: 'var(--text)' }}
        >
          Comparison
        </h3>
        <button
          onClick={onClose}
          className="text-xs cursor-pointer"
          style={{
            color: 'var(--text-muted)',
            background: 'transparent',
            border: 'none',
          }}
        >
          Close
        </button>
      </div>
      <div
        className="grid grid-cols-3 gap-4
          items-center text-center"
      >
        <div>
          <p
            className="text-xs mb-2"
            style={{ color: 'var(--text-muted)' }}
          >
            {new Date(
              a.timestamp,
            ).toLocaleDateString()}
          </p>
          <HealthRing
            score={a.healthScore}
            size={100}
          />
        </div>
        <div>
          <span
            className="text-2xl font-bold"
            style={{ color: diffColor }}
          >
            {diff > 0 ? '+' : ''}
            {diff}
          </span>
          <p
            className="text-xs mt-1"
            style={{ color: 'var(--text-muted)' }}
          >
            score change
          </p>
        </div>
        <div>
          <p
            className="text-xs mb-2"
            style={{ color: 'var(--text-muted)' }}
          >
            {new Date(
              b.timestamp,
            ).toLocaleDateString()}
          </p>
          <HealthRing
            score={b.healthScore}
            size={100}
          />
        </div>
      </div>
    </div>
  );
}

/* ── Empty State ───────────────────────────────── */

function EmptyState({
  onRun,
}: {
  onRun: () => void;
}) {
  return (
    <div
      className="glass-card-solid p-12
        text-center"
    >
      <Activity
        size={48}
        className="mx-auto mb-4"
        style={{ color: 'var(--border-strong)' }}
      />
      <h2
        className="text-lg font-semibold mb-2"
        style={{ color: 'var(--text)' }}
      >
        No analysis history yet
      </h2>
      <p
        className="text-sm mb-6"
        style={{ color: 'var(--text-muted)' }}
      >
        Run your first analysis to start
        tracking network health over time.
      </p>
      <button
        onClick={onRun}
        className="px-6 py-2.5 rounded-xl
          font-medium text-sm cursor-pointer
          transition-colors"
        style={{
          background: 'var(--primary)',
          color: '#fff',
          border: 'none',
        }}
      >
        Run your first analysis
      </button>
    </div>
  );
}

/* ── Main Page ─────────────────────────────────── */

export default function HistoryPage() {
  const navigate = useNavigate();
  const [entries, setEntries] = useState<
    AnalysisRecord[]
  >([]);
  const [loading, setLoading] = useState(true);
  const [compareMode, setCompareMode] =
    useState(false);
  const [selected, setSelected] = useState<
    number[]
  >([]);

  useEffect(() => {
    getHistory()
      .then(setEntries)
      .finally(() => setLoading(false));
  }, []);

  async function handleDelete(id: number) {
    await deleteAnalysis(id);
    setEntries((p) =>
      p.filter((e) => e.id !== id),
    );
    setSelected((p) =>
      p.filter((s) => s !== id),
    );
  }

  function toggleSelect(id: number) {
    setSelected((prev) => {
      if (prev.includes(id))
        return prev.filter((s) => s !== id);
      if (prev.length >= 2) return prev;
      return [...prev, id];
    });
  }

  if (loading) {
    return (
      <div
        className="max-w-4xl mx-auto space-y-3"
      >
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="glass-card-solid p-6 h-20
              animate-pulse"
            style={{
              background: 'var(--bg-elevated)',
            }}
          />
        ))}
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="max-w-4xl mx-auto">
        <EmptyState
          onRun={() =>
            navigate('/analysis/new')
          }
        />
      </div>
    );
  }

  const compareA =
    selected.length === 2
      ? entries.find(
          (e) => e.id === selected[0],
        )
      : undefined;
  const compareB =
    selected.length === 2
      ? entries.find(
          (e) => e.id === selected[1],
        )
      : undefined;

  return (
    <div
      className="max-w-4xl mx-auto space-y-4"
    >
      {/* Toolbar */}
      <div className="flex justify-end">
        <button
          onClick={() => {
            setCompareMode(!compareMode);
            setSelected([]);
          }}
          className="flex items-center gap-2
            px-4 py-2 rounded-lg text-xs
            font-medium cursor-pointer
            transition-colors"
          style={{
            background: compareMode
              ? 'var(--primary)'
              : 'var(--bg-elevated)',
            color: compareMode
              ? '#fff'
              : 'var(--text-muted)',
            border: 'none',
          }}
        >
          <GitCompare size={14} />
          {compareMode ? 'Cancel' : 'Compare'}
        </button>
      </div>

      {/* Compare Panel */}
      {compareA && compareB && (
        <CompareView
          a={compareA}
          b={compareB}
          onClose={() => {
            setCompareMode(false);
            setSelected([]);
          }}
        />
      )}

      {/* Timeline */}
      {entries.map((entry) => (
        <div
          key={entry.id}
          className="glass-card-solid p-5 flex
            items-center gap-4 transition-opacity
            hover:opacity-90"
          role="button"
          tabIndex={0}
          onClick={() => {
            if (compareMode && entry.id != null) {
              toggleSelect(entry.id);
            } else {
              navigate(
                `/analysis/${entry.jobId}`,
              );
            }
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              navigate(
                `/analysis/${entry.jobId}`,
              );
            }
          }}
          style={{
            cursor: 'pointer',
            outline:
              compareMode &&
              entry.id != null &&
              selected.includes(entry.id)
                ? '2px solid var(--primary)'
                : 'none',
          }}
        >
          {/* Score */}
          <ScoreBadge score={entry.healthScore} />

          {/* Info */}
          <div className="flex-1 min-w-0">
            <p
              className="text-sm font-medium"
              style={{ color: 'var(--text)' }}
            >
              {new Date(
                entry.timestamp,
              ).toLocaleString()}
            </p>
            <p
              className="text-xs truncate"
              style={{
                color: 'var(--text-muted)',
              }}
            >
              {entry.summary}
            </p>
          </div>

          {/* Stats */}
          <div
            className="hidden sm:flex
              items-center gap-4"
          >
            <span
              className="flex items-center
                gap-1 text-xs"
              style={{
                color: 'var(--text-muted)',
              }}
            >
              <Radio size={12} />
              {entry.apCount}
            </span>
            <span
              className="flex items-center
                gap-1 text-xs"
              style={{
                color: 'var(--text-muted)',
              }}
            >
              <Users size={12} />
              {entry.clientCount}
            </span>
          </div>

          {/* Delete */}
          {!compareMode && entry.id != null && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                void handleDelete(entry.id!);
              }}
              className="p-2 rounded-lg
                cursor-pointer transition-colors"
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--text-muted)',
              }}
              aria-label="Delete entry"
            >
              <Trash2 size={16} />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
