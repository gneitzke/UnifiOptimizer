import {
  useState,
  useEffect,
  useCallback,
} from 'react';
import {
  useParams,
  useNavigate,
} from 'react-router-dom';
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import {
  Users,
  Radio,
  AlertTriangle,
  CheckCircle2,
  ArrowUpDown,
  Eye,
} from 'lucide-react';
import type {
  AnalysisJob,
  AnalysisResult,
  ApAnalysis,
  ClientAnalysis,
  Finding,
} from '../../types/api';
import * as api from '../../services/api';
import StatCard from '../dashboard/StatCard';

/* ── Tabs ──────────────────────────────────────── */

type Tab =
  | 'overview'
  | 'devices'
  | 'clients'
  | 'channels'
  | 'recommendations';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'devices', label: 'Devices' },
  { key: 'clients', label: 'Clients' },
  { key: 'channels', label: 'Channels' },
  { key: 'recommendations', label: 'Recs' },
];

/* ── Chart Colors ──────────────────────────────── */

const PIE_COLORS = [
  '#0088ff', '#00c48f',
  '#ffb800', '#ff4757',
  '#a855f7',
];

/* ── Skeleton ──────────────────────────────────── */

function SkeletonCard() {
  return (
    <div
      className="glass-card-solid p-6 h-32
        animate-pulse"
      style={{
        background: 'var(--bg-elevated)',
      }}
    />
  );
}

/* ── Priority Badge ────────────────────────────── */

function PriorityBadge({
  severity,
}: {
  severity: string;
}) {
  const map: Record<string, string> = {
    critical: 'var(--error)',
    warning: 'var(--warning)',
    info: 'var(--primary)',
  };
  const c = map[severity] ?? 'var(--text-muted)';
  return (
    <span
      className="text-[10px] uppercase
        font-semibold px-2 py-0.5 rounded-full"
      style={{
        background: `color-mix(
          in srgb, ${c} 20%, transparent
        )`,
        color: c,
      }}
    >
      {severity}
    </span>
  );
}

/* ── Health Bar ────────────────────────────────── */

function HealthBar({
  label,
  value,
}: {
  label: string;
  value: number;
}) {
  const color =
    value > 80
      ? 'var(--success)'
      : value > 60
        ? 'var(--warning)'
        : 'var(--error)';
  return (
    <div className="space-y-1">
      <div className="flex justify-between">
        <span
          className="text-xs"
          style={{ color: 'var(--text-muted)' }}
        >
          {label}
        </span>
        <span
          className="text-xs font-semibold"
          style={{ color }}
        >
          {value}
        </span>
      </div>
      <div
        className="h-2 rounded-full overflow-hidden"
        style={{
          background: 'var(--bg-elevated)',
        }}
      >
        <div
          className="h-full rounded-full"
          style={{
            width: `${value}%`,
            background: color,
            transition: 'width 0.8s ease',
          }}
        />
      </div>
    </div>
  );
}

/* ── Overview Tab ──────────────────────────────── */

function OverviewTab({
  result,
}: {
  result: AnalysisResult;
}) {
  const { health, aps, clients } = result;

  const signalBuckets = [
    { name: 'Excellent', value: 0 },
    { name: 'Good', value: 0 },
    { name: 'Fair', value: 0 },
    { name: 'Poor', value: 0 },
  ];
  clients.forEach((c) => {
    if (c.signal >= -50) signalBuckets[0].value++;
    else if (c.signal >= -65)
      signalBuckets[1].value++;
    else if (c.signal >= -75)
      signalBuckets[2].value++;
    else signalBuckets[3].value++;
  });

  const bandData = [
    {
      name: '2.4 GHz',
      value: aps.filter(
        (a) => a.band === '2.4GHz',
      ).length,
    },
    {
      name: '5 GHz',
      value: aps.filter(
        (a) => a.band === '5GHz',
      ).length,
    },
    {
      name: '6 GHz',
      value: aps.filter(
        (a) => a.band === '6GHz',
      ).length,
    },
  ].filter((d) => d.value > 0);

  return (
    <div className="space-y-6">
      {/* Health Breakdown */}
      <div className="glass-card-solid p-6">
        <h3
          className="text-sm font-semibold mb-4"
          style={{ color: 'var(--text)' }}
        >
          Health Score Breakdown
        </h3>
        <div className="space-y-3">
          <HealthBar
            label="RF Quality"
            value={health.wireless}
          />
          <HealthBar
            label="Client Health"
            value={health.coverage}
          />
          <HealthBar
            label="Infrastructure"
            value={health.wired}
          />
          <HealthBar
            label="Latency"
            value={health.latency}
          />
        </div>
      </div>

      {/* Charts Row */}
      <div
        className="grid grid-cols-1 md:grid-cols-2
          gap-4"
      >
        <div className="glass-card-solid p-6">
          <h3
            className="text-sm font-semibold mb-2"
            style={{ color: 'var(--text)' }}
          >
            Signal Distribution
          </h3>
          <ResponsiveContainer
            width="100%"
            height={200}
          >
            <PieChart>
              <Pie
                data={signalBuckets.filter(
                  (s) => s.value > 0,
                )}
                dataKey="value"
                nameKey="name"
                innerRadius={50}
                outerRadius={80}
                paddingAngle={3}
              >
                {signalBuckets.map((_, i) => (
                  <Cell
                    key={i}
                    fill={
                      PIE_COLORS[
                        i % PIE_COLORS.length
                      ]
                    }
                  />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </div>

        <div className="glass-card-solid p-6">
          <h3
            className="text-sm font-semibold mb-2"
            style={{ color: 'var(--text)' }}
          >
            Band Usage
          </h3>
          <ResponsiveContainer
            width="100%"
            height={200}
          >
            <PieChart>
              <Pie
                data={bandData}
                dataKey="value"
                nameKey="name"
                innerRadius={50}
                outerRadius={80}
                paddingAngle={3}
              >
                {bandData.map((_, i) => (
                  <Cell
                    key={i}
                    fill={
                      PIE_COLORS[
                        i % PIE_COLORS.length
                      ]
                    }
                  />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Stat Cards */}
      <div
        className="grid grid-cols-1
          sm:grid-cols-2 lg:grid-cols-4 gap-4"
      >
        <StatCard
          title="Overall Health"
          value={health.overall}
          icon={CheckCircle2}
        />
        <StatCard
          title="Access Points"
          value={result.apCount}
          icon={Radio}
        />
        <StatCard
          title="Clients"
          value={result.clientCount}
          icon={Users}
        />
        <StatCard
          title="Findings"
          value={result.findings.length}
          icon={AlertTriangle}
          iconColor="var(--warning)"
        />
      </div>
    </div>
  );
}

/* ── Devices Tab ───────────────────────────────── */

type SortField =
  | 'name'
  | 'clients'
  | 'satisfaction';

function DevicesTab({
  aps,
}: {
  aps: ApAnalysis[];
}) {
  const [sortBy, setSortBy] =
    useState<SortField>('name');
  const [asc, setAsc] = useState(true);

  function handleSort(f: SortField) {
    if (sortBy === f) setAsc(!asc);
    else {
      setSortBy(f);
      setAsc(true);
    }
  }

  const sorted = [...aps].sort((a, b) => {
    const dir = asc ? 1 : -1;
    if (sortBy === 'name')
      return dir * a.name.localeCompare(b.name);
    return dir * (a[sortBy] - b[sortBy]);
  });

  const thStyle = {
    color: 'var(--text-muted)',
    borderBottom: '1px solid var(--border)',
  };

  return (
    <div
      className="glass-card-solid overflow-x-auto"
    >
      <table className="w-full text-sm">
        <thead>
          <tr>
            {(
              [
                ['name', 'Name'],
                ['model', 'Model'],
                ['channel', 'Channel'],
                ['txPower', 'Power'],
                ['clients', 'Clients'],
                ['band', 'Band'],
                ['satisfaction', 'Health'],
              ] as const
            ).map(([key, label]) => (
              <th
                key={key}
                className="text-left px-4 py-3
                  text-xs font-medium cursor-pointer
                  select-none"
                style={thStyle}
                onClick={() =>
                  handleSort(
                    key as SortField,
                  )
                }
              >
                <span className="inline-flex
                  items-center gap-1"
                >
                  {label}
                  {sortBy === key && (
                    <ArrowUpDown size={12} />
                  )}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((ap) => (
            <tr
              key={ap.mac}
              className="hover:opacity-80
                transition-opacity"
              style={{
                borderBottom:
                  '1px solid var(--border)',
              }}
            >
              <td
                className="px-4 py-3 font-medium"
                style={{ color: 'var(--text)' }}
              >
                {ap.name}
              </td>
              <td
                className="px-4 py-3"
                style={{
                  color: 'var(--text-muted)',
                }}
              >
                {ap.model}
              </td>
              <td
                className="px-4 py-3"
                style={{
                  color: 'var(--text-muted)',
                }}
              >
                {ap.channel}
              </td>
              <td
                className="px-4 py-3"
                style={{
                  color: 'var(--text-muted)',
                }}
              >
                {ap.txPower} dBm
              </td>
              <td
                className="px-4 py-3"
                style={{
                  color: 'var(--text-muted)',
                }}
              >
                {ap.clients}
              </td>
              <td className="px-4 py-3">
                <span
                  className="text-[10px]
                    font-semibold px-2 py-0.5
                    rounded-full uppercase"
                  style={{
                    background:
                      'rgba(0,136,255,0.15)',
                    color: 'var(--primary)',
                  }}
                >
                  {ap.band}
                </span>
              </td>
              <td className="px-4 py-3">
                <span
                  style={{
                    color:
                      ap.satisfaction > 80
                        ? 'var(--success)'
                        : ap.satisfaction > 60
                          ? 'var(--warning)'
                          : 'var(--error)',
                  }}
                >
                  {ap.satisfaction}%
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Clients Tab ───────────────────────────────── */

function IssueBadge({
  issue,
}: {
  issue: string;
}) {
  const lower = issue.toLowerCase();
  const color = lower.includes('dead')
    ? 'var(--error)'
    : lower.includes('weak')
      ? 'var(--warning)'
      : 'var(--primary)';
  return (
    <span
      className="text-[10px] font-semibold
        px-2 py-0.5 rounded-full"
      style={{
        background: `color-mix(
          in srgb, ${color} 20%, transparent
        )`,
        color,
      }}
    >
      {issue}
    </span>
  );
}

function ClientsTab({
  clients,
}: {
  clients: ClientAnalysis[];
}) {
  const problem = clients.filter(
    (c) => c.issues.length > 0,
  );

  if (problem.length === 0) {
    return (
      <div
        className="glass-card-solid p-8
          text-center"
      >
        <CheckCircle2
          size={32}
          className="mx-auto mb-2"
          style={{ color: 'var(--success)' }}
        />
        <p style={{ color: 'var(--text-muted)' }}>
          No client issues detected
        </p>
      </div>
    );
  }

  return (
    <div
      className="glass-card-solid overflow-x-auto"
    >
      <table className="w-full text-sm">
        <thead>
          <tr>
            {['Client', 'Signal', 'Issues'].map(
              (h) => (
                <th
                  key={h}
                  className="text-left px-4
                    py-3 text-xs font-medium"
                  style={{
                    color: 'var(--text-muted)',
                    borderBottom:
                      '1px solid var(--border)',
                  }}
                >
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {problem.map((c) => (
            <tr
              key={c.mac}
              style={{
                borderBottom:
                  '1px solid var(--border)',
              }}
            >
              <td
                className="px-4 py-3 font-medium"
                style={{ color: 'var(--text)' }}
              >
                {c.hostname || c.mac}
              </td>
              <td
                className="px-4 py-3"
                style={{
                  color:
                    c.signal >= -65
                      ? 'var(--success)'
                      : c.signal >= -75
                        ? 'var(--warning)'
                        : 'var(--error)',
                }}
              >
                {c.signal} dBm
              </td>
              <td
                className="px-4 py-3 flex
                  flex-wrap gap-1"
              >
                {c.issues.map((issue) => (
                  <IssueBadge
                    key={issue}
                    issue={issue}
                  />
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Channels Tab ──────────────────────────────── */

function ChannelsTab({
  aps,
}: {
  aps: ApAnalysis[];
}) {
  const channelMap = new Map<
    number,
    { count: number; band: string }
  >();
  aps.forEach((ap) => {
    const e = channelMap.get(ap.channel);
    if (e) e.count++;
    else
      channelMap.set(ap.channel, {
        count: 1,
        band: ap.band,
      });
  });
  const channels = [...channelMap.entries()]
    .sort(([a], [b]) => a - b);

  return (
    <div className="glass-card-solid p-6">
      <h3
        className="text-sm font-semibold mb-4"
        style={{ color: 'var(--text)' }}
      >
        Channel Distribution
      </h3>
      <div className="space-y-3">
        {channels.map(([ch, { count, band }]) => (
          <div
            key={ch}
            className="flex items-center gap-3"
          >
            <span
              className="text-xs w-20
                font-mono shrink-0"
              style={{ color: 'var(--text)' }}
            >
              Ch {ch}
              <span
                className="ml-1 text-[10px]"
                style={{
                  color: 'var(--text-muted)',
                }}
              >
                {band}
              </span>
            </span>
            <div
              className="flex-1 h-4 rounded-full
                overflow-hidden"
              style={{
                background: 'var(--bg-elevated)',
              }}
            >
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.min(
                    (count / aps.length) * 100,
                    100,
                  )}%`,
                  background:
                    count > 3
                      ? 'var(--warning)'
                      : 'var(--primary)',
                  transition: 'width 0.6s ease',
                }}
              />
            </div>
            <span
              className="text-xs w-6 text-right"
              style={{
                color: 'var(--text-muted)',
              }}
            >
              {count}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Recommendations Tab ───────────────────────── */

function RecsTab({
  findings,
  onPreview,
}: {
  findings: Finding[];
  onPreview: (ids: string[]) => void;
}) {
  const [selected, setSelected] = useState<
    Set<string>
  >(new Set());

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="space-y-4">
      {findings.map((f) => (
        <label
          key={f.id}
          className="glass-card-solid p-5 flex
            items-start gap-4 cursor-pointer
            hover:opacity-90 transition-opacity"
        >
          <input
            type="checkbox"
            checked={selected.has(f.id)}
            onChange={() => toggle(f.id)}
            className="mt-1 shrink-0
              accent-[var(--primary)]"
          />
          <div className="flex-1 min-w-0">
            <div
              className="flex items-center
                gap-2 mb-1"
            >
              <PriorityBadge
                severity={f.severity}
              />
              <span
                className="text-xs"
                style={{
                  color: 'var(--text-muted)',
                }}
              >
                {f.category}
              </span>
            </div>
            <h4
              className="text-sm font-medium"
              style={{ color: 'var(--text)' }}
            >
              {f.title}
            </h4>
            <p
              className="text-xs mt-1"
              style={{
                color: 'var(--text-muted)',
              }}
            >
              {f.description}
            </p>
          </div>
        </label>
      ))}

      {selected.size > 0 && (
        <button
          onClick={() =>
            onPreview([...selected])
          }
          className="w-full py-3 rounded-xl
            font-medium text-sm cursor-pointer
            transition-colors"
          style={{
            background: 'var(--primary)',
            color: '#fff',
            border: 'none',
          }}
        >
          <Eye
            size={16}
            className="inline mr-2 -mt-0.5"
          />
          Preview {selected.size} Selected
        </button>
      )}
    </div>
  );
}

/* ── Main Page ─────────────────────────────────── */

export default function AnalysisPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [job, setJob] =
    useState<AnalysisJob | null>(null);
  const [result, setResult] =
    useState<AnalysisResult | null>(null);
  const [error, setError] =
    useState<string | null>(null);
  const [tab, setTab] =
    useState<Tab>('overview');

  const poll = useCallback(async () => {
    if (!id) return;
    try {
      const j = await api.getAnalysisStatus(id);
      setJob(j);
      if (j.status === 'completed') {
        const r =
          await api.getAnalysisResults(id);
        setResult(r);
      }
      if (j.status === 'failed') {
        setError(j.error ?? 'Analysis failed');
      }
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : 'Unknown error',
      );
    }
  }, [id]);

  useEffect(() => {
    void poll();
    const iv = setInterval(() => {
      if (
        !result &&
        !error
      ) {
        void poll();
      }
    }, 2000);
    return () => clearInterval(iv);
  }, [poll, result, error]);

  /* Loading skeleton */
  if (!result && !error) {
    return (
      <div
        className="max-w-6xl mx-auto space-y-4"
      >
        <div
          className="glass-card-solid p-6"
        >
          <p
            className="text-sm"
            style={{ color: 'var(--text-muted)' }}
          >
            {job
              ? `Analyzing… ${job.progress}%`
              : 'Starting analysis…'}
          </p>
          {job && (
            <div
              className="mt-3 h-2 rounded-full
                overflow-hidden"
              style={{
                background: 'var(--bg-elevated)',
              }}
            >
              <div
                className="h-full rounded-full"
                style={{
                  width: `${job.progress}%`,
                  background: 'var(--primary)',
                  transition: 'width 0.5s ease',
                }}
              />
            </div>
          )}
        </div>
        <div
          className="grid grid-cols-1
            sm:grid-cols-2 lg:grid-cols-4 gap-4"
        >
          {[1, 2, 3, 4].map((i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="glass-card-solid p-8
          text-center"
      >
        <AlertTriangle
          size={32}
          className="mx-auto mb-2"
          style={{ color: 'var(--error)' }}
        />
        <p style={{ color: 'var(--text)' }}>
          {error}
        </p>
      </div>
    );
  }

  if (!result) return null;

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Tab Bar */}
      <nav
        className="flex gap-1 p-1 rounded-xl"
        style={{
          background: 'var(--bg-elevated)',
        }}
      >
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className="flex-1 py-2 text-xs
              font-medium rounded-lg
              cursor-pointer transition-all"
            style={{
              background:
                tab === t.key
                  ? 'var(--primary)'
                  : 'transparent',
              color:
                tab === t.key
                  ? '#fff'
                  : 'var(--text-muted)',
              border: 'none',
            }}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {/* Tab Content */}
      {tab === 'overview' && (
        <OverviewTab result={result} />
      )}
      {tab === 'devices' && (
        <DevicesTab aps={result.aps} />
      )}
      {tab === 'clients' && (
        <ClientsTab clients={result.clients} />
      )}
      {tab === 'channels' && (
        <ChannelsTab aps={result.aps} />
      )}
      {tab === 'recommendations' && (
        <RecsTab
          findings={result.findings}
          onPreview={(ids) => {
            navigate(
              `/repair?findings=${ids.join(',')}`,
            );
          }}
        />
      )}
    </div>
  );
}
