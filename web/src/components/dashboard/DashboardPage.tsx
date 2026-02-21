import { useNavigate } from 'react-router-dom';
import {
  Activity,
  History,
  Wrench,
  Wifi,
  Users,
  Radio,
  MapPin,
} from 'lucide-react';

/* ── Health Ring SVG ───────────────────────────── */

function HealthRing({
  score,
}: {
  score: number;
}) {
  const r = 70;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - score / 100);

  return (
    <div className="relative w-44 h-44 mx-auto">
      <svg
        viewBox="0 0 160 160"
        className="w-full h-full"
      >
        {/* Track */}
        <circle
          cx="80" cy="80" r={r}
          fill="none"
          stroke="var(--border-strong)"
          strokeWidth="8"
        />
        {/* Score arc */}
        <circle
          cx="80" cy="80" r={r}
          fill="none"
          stroke="var(--primary)"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={offset}
          transform="rotate(-90 80 80)"
          style={{
            transition:
              'stroke-dashoffset 1s ease',
          }}
        />
      </svg>
      <div
        className="absolute inset-0 flex flex-col
          items-center justify-center"
      >
        <span
          className="text-3xl font-bold"
          style={{ color: 'var(--text)' }}
        >
          {score}
        </span>
        <span
          className="text-xs"
          style={{ color: 'var(--text-muted)' }}
        >
          Health
        </span>
      </div>
    </div>
  );
}

/* ── Stat Card ─────────────────────────────────── */

function StatCard({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
}) {
  return (
    <div
      className="glass-card-solid p-5 flex
        items-center gap-4"
    >
      <div
        className="w-10 h-10 rounded-xl flex
          items-center justify-center shrink-0"
        style={{
          background: 'rgba(0,136,255,0.1)',
        }}
      >
        <Icon
          size={20}
          style={{ color: 'var(--primary)' }}
        />
      </div>
      <div>
        <p
          className="text-xs"
          style={{ color: 'var(--text-muted)' }}
        >
          {label}
        </p>
        <p
          className="text-lg font-semibold"
          style={{ color: 'var(--text)' }}
        >
          {value}
        </p>
      </div>
    </div>
  );
}

/* ── Quick Action ──────────────────────────────── */

function QuickAction({
  icon: Icon,
  label,
  color,
  onClick,
}: {
  icon: React.ElementType;
  label: string;
  color: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="glass-card-solid p-4 flex
        flex-col items-center gap-2
        cursor-pointer transition-transform
        hover:scale-[1.03]"
      style={{ border: 'none' }}
    >
      <Icon size={22} style={{ color }} />
      <span
        className="text-xs font-medium"
        style={{ color: 'var(--text)' }}
      >
        {label}
      </span>
    </button>
  );
}

/* ── Badge ─────────────────────────────────────── */

const BADGE_COLORS: Record<string, string> = {
  critical: 'var(--error)',
  warning: 'var(--warning)',
  info: 'var(--primary)',
};

function PriorityBadge({
  severity,
}: {
  severity: string;
}) {
  const bg = BADGE_COLORS[severity] ?? '#666';
  return (
    <span
      className="text-[10px] font-semibold
        uppercase px-2 py-0.5 rounded-full"
      style={{
        background: `${bg}22`,
        color: bg,
      }}
    >
      {severity}
    </span>
  );
}

/* ── Main Dashboard ────────────────────────────── */

export default function DashboardPage() {
  const navigate = useNavigate();

  // Placeholder data until real API wired
  const health = 82;
  const findings = [
    {
      id: '1',
      severity: 'critical',
      title: 'Co-channel interference on 5 GHz',
    },
    {
      id: '2',
      severity: 'warning',
      title: '3 APs with >40 clients',
    },
    {
      id: '3',
      severity: 'info',
      title: 'Firmware update available',
    },
  ];

  return (
    <div className="max-w-6xl mx-auto space-y-8">
      {/* ── Hero: Health Score ─────────── */}
      <section
        className="glass-card p-8 text-center"
      >
        <HealthRing score={health} />
        <p
          className="text-sm mt-3"
          style={{ color: 'var(--text-muted)' }}
        >
          Network is performing well
        </p>
      </section>

      {/* ── Stat cards ────────────────── */}
      <section
        className="grid grid-cols-1
          sm:grid-cols-2 lg:grid-cols-4 gap-4"
      >
        <StatCard
          icon={Radio}
          label="Access Points"
          value={12}
        />
        <StatCard
          icon={Users}
          label="Clients"
          value={87}
        />
        <StatCard
          icon={Wifi}
          label="Wireless %"
          value="94%"
        />
        <StatCard
          icon={MapPin}
          label="Coverage"
          value="Good"
        />
      </section>

      {/* ── Quick Actions ─────────────── */}
      <section
        className="grid grid-cols-3 gap-4"
      >
        <QuickAction
          icon={Activity}
          label="Run Analysis"
          color="var(--primary)"
          onClick={() =>
            navigate('/analysis/new')
          }
        />
        <QuickAction
          icon={History}
          label="View History"
          color="var(--success)"
          onClick={() => navigate('/history')}
        />
        <QuickAction
          icon={Wrench}
          label="Repair Mode"
          color="var(--warning)"
          onClick={() => navigate('/repair')}
        />
      </section>

      {/* ── Recent Findings ───────────── */}
      <section className="glass-card-solid p-6">
        <h2
          className="text-sm font-semibold mb-4"
          style={{ color: 'var(--text)' }}
        >
          Recent Findings
        </h2>
        <ul className="space-y-3">
          {findings.map((f) => (
            <li
              key={f.id}
              className="flex items-center
                justify-between py-2 px-3
                rounded-lg"
              style={{
                background:
                  'var(--bg-elevated)',
              }}
            >
              <span
                className="text-sm"
                style={{
                  color: 'var(--text)',
                }}
              >
                {f.title}
              </span>
              <PriorityBadge
                severity={f.severity}
              />
            </li>
          ))}
        </ul>
      </section>

      {/* ── Topology placeholder ──────── */}
      <section className="glass-card-solid p-8
        text-center"
      >
        <p
          className="text-sm"
          style={{ color: 'var(--text-muted)' }}
        >
          Network topology diagram coming soon
        </p>
      </section>
    </div>
  );
}
