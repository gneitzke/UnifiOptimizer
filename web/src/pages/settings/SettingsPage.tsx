import type { ReactNode } from 'react';
import { PlayCircle } from 'lucide-react';
import { Button, Card, EmptyState, Skeleton } from '../../components/ui';
import { useHealth, type Health, type JobHealth } from '../../api';
import { startTour } from '../onboarding';
import { AccessTokenSection } from './AccessTokenSection';

/**
 * /settings — read-only view of the daemon's live configuration and health. No
 * editing in v1 (never-do: no fake affordances). The API exposes /api/health but
 * not yet a /api/system/config, so controller host and detector thresholds are
 * shown as honest "not exposed yet" sections rather than invented. Home
 * Assistant and the LLM investigator are configured elsewhere (data/config.yaml
 * and per-issue, respectively) and are described here for orientation only —
 * this page does not edit either. When a config endpoint ships, wire it here
 * (see INTEGRATION note) — the redactHost helper is already in place for
 * host-only display.
 */

function formatBytes(n: number | null): string {
  if (n == null) return 'UNKNOWN';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

function formatUptime(s: number): string {
  if (!Number.isFinite(s) || s < 0) return 'UNKNOWN';
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatCadence(s: number | null): string {
  if (s == null) return 'on demand';
  if (s < 60) return `every ${s}s`;
  if (s < 3600) return `every ${Math.round(s / 60)}m`;
  if (s < 86400) return `every ${Math.round(s / 3600)}h`;
  return `every ${Math.round(s / 86400)}d`;
}

function formatAge(age: number | 'UNKNOWN'): string {
  if (age === 'UNKNOWN' || age == null) return 'never';
  if (age < 60) return `${Math.round(age)}s ago`;
  if (age < 3600) return `${Math.round(age / 60)}m ago`;
  if (age < 86400) return `${Math.round(age / 3600)}h ago`;
  return `${Math.round(age / 86400)}d ago`;
}

/** Reduce a controller URL/host to host-only (no scheme, port, path, creds). */
function redactHost(raw: string): string {
  try {
    const u = new URL(raw.includes('://') ? raw : `https://${raw}`);
    return u.hostname;
  } catch {
    return raw.replace(/^\w+:\/\//, '').split(/[/:]/)[0];
  }
}
// Referenced so the helper ships wired-and-ready for the future config endpoint.
void redactHost;

const STATUS_TONE: Record<string, { color: string; label: string }> = {
  ok: { color: 'var(--sev-healthy)', label: 'Healthy' },
  stale: { color: 'var(--sev-p3)', label: 'Stale' },
  failing: { color: 'var(--sev-p1)', label: 'Failing' },
  UNKNOWN: { color: 'var(--sev-neutral)', label: 'Unknown' },
  degraded: { color: 'var(--sev-p2)', label: 'Degraded' },
  starting: { color: 'var(--sev-neutral)', label: 'Starting' },
};

function StatusDot({ status }: { status: string }) {
  const tone = STATUS_TONE[status] ?? STATUS_TONE.UNKNOWN;
  return (
    <span className="inline-flex items-center gap-1.5 t-secondary" style={{ color: tone.color }}>
      <span
        aria-hidden
        className="inline-block w-2 h-2 rounded-full"
        style={{ background: tone.color }}
      />
      {tone.label}
    </span>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="mb-6">
      <div className="t-section mb-1" style={{ color: 'var(--fg)' }}>
        {title}
      </div>
      {description && (
        <p className="t-secondary mb-3" style={{ color: 'var(--fg-muted)' }}>
          {description}
        </p>
      )}
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      className="flex items-center justify-between gap-4 px-4 py-2.5"
      style={{ borderBottom: '1px solid var(--hairline)' }}
    >
      <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
        {label}
      </span>
      <span className="t-body text-right" style={{ color: 'var(--fg)' }}>
        {children}
      </span>
    </div>
  );
}

function PhasePlaceholder({ note }: { note: string }) {
  return (
    <Card className="flex items-center gap-3">
      <span
        className="inline-flex items-center justify-center w-2 h-2 rounded-full shrink-0"
        style={{ background: 'var(--sev-neutral)' }}
        aria-hidden
      />
      <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
        {note}
      </span>
    </Card>
  );
}

function SystemSection({ health }: { health: Health }) {
  const dbName = health.db.path === 'UNKNOWN' ? 'UNKNOWN' : health.db.path.split(/[/\\]/).pop();
  const byType = Object.entries(health.entities.by_type ?? {});
  return (
    <Card pad="none">
      <Row label="Status">
        <StatusDot status={health.status} />
      </Row>
      <Row label="Uptime">
        <span className="tnum">{formatUptime(health.uptime_s)}</span>
      </Row>
      <Row label="WebSocket">
        <span style={{ color: 'var(--fg)' }}>{health.websocket.state}</span>
        {health.websocket.detail && (
          <span className="t-caption ml-2" style={{ color: 'var(--fg-subtle)' }}>
            {health.websocket.detail}
          </span>
        )}
      </Row>
      <Row label="Backfill">
        <span style={{ color: 'var(--fg)' }}>{health.backfill}</span>
      </Row>
      <Row label="Database">
        <span className="font-mono" style={{ fontSize: 13 }}>
          {dbName}
        </span>
        <span className="t-caption tnum ml-2" style={{ color: 'var(--fg-subtle)' }}>
          {formatBytes(health.db.size_bytes)}
        </span>
      </Row>
      <Row label="Entities">
        <span className="tnum">
          {health.entities.total === 'UNKNOWN' ? 'UNKNOWN' : health.entities.total}
        </span>
        {byType.length > 0 && (
          <span className="t-caption tnum ml-2" style={{ color: 'var(--fg-subtle)' }}>
            {byType.map(([t, n]) => `${n} ${t}`).join(' · ')}
          </span>
        )}
      </Row>
    </Card>
  );
}

function CadencesSection({ jobs }: { jobs: JobHealth[] }) {
  if (jobs.length === 0) {
    return (
      <PhasePlaceholder note="No collector jobs reported yet." />
    );
  }
  return (
    <Card pad="none">
      <div
        className="grid px-4 h-9 items-center t-label"
        style={{
          gridTemplateColumns: '1fr 120px 120px 100px',
          gap: 12,
          color: 'var(--fg-muted)',
          borderBottom: '1px solid var(--hairline)',
        }}
      >
        <span>Job</span>
        <span className="text-right">Cadence</span>
        <span className="text-right">Last success</span>
        <span className="text-right">State</span>
      </div>
      {jobs.map((j) => (
        <div
          key={j.job}
          className="grid px-4 py-2.5 items-center"
          style={{
            gridTemplateColumns: '1fr 120px 120px 100px',
            gap: 12,
            borderBottom: '1px solid var(--hairline)',
          }}
        >
          <span style={{ color: 'var(--fg)' }}>{j.job}</span>
          <span className="text-right tnum t-secondary" style={{ color: 'var(--fg-muted)' }}>
            {formatCadence(j.interval_s)}
          </span>
          <span className="text-right tnum t-secondary" style={{ color: 'var(--fg-muted)' }}>
            {formatAge(j.last_success_age_s)}
          </span>
          <span className="flex justify-end">
            <StatusDot status={j.status} />
          </span>
        </div>
      ))}
    </Card>
  );
}

export default function SettingsPage() {
  const { data: health, loading, error, reload } = useHealth();

  return (
    <div className="px-6 sm:px-8 py-8" style={{ maxWidth: 760, marginInline: 'auto' }}>
      <h2 className="t-page-title mb-1" style={{ color: 'var(--fg)' }}>
        Settings
      </h2>
      <p className="t-secondary mb-6" style={{ color: 'var(--fg-muted)' }}>
        The daemon's live configuration and health. Read-only in this version;
        editing arrives with the config surface in a later phase.
      </p>

      <Section
        title="Guided tour"
        description="A short walkthrough of the health verdict, service levels, issues, and device drilldown. It runs once on first sign-in."
      >
        <Card className="flex items-center justify-between gap-4">
          <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
            Replay the first-run walkthrough anytime.
          </span>
          <Button variant="secondary" size="sm" onClick={() => startTour()}>
            <PlayCircle size={15} />
            Replay tour
          </Button>
        </Card>
      </Section>

      <Section
        title="Demo network"
        description="Explore UnifiOptimizer without a controller, using a fictional, PII-free network."
      >
        <Card>
          <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
            Generate a demo database with{' '}
            <span className="font-mono" style={{ fontSize: 12 }}>
              python3 -m netadmin.cli demo-seed
            </span>{' '}
            and serve it read-only. See{' '}
            <span className="font-mono" style={{ fontSize: 12 }}>
              docs/README
            </span>{' '}
            for the demo walkthrough. No controller access, no live network.
          </p>
        </Card>
      </Section>

      {loading && !health ? (
        <Card>
          <Skeleton className="h-6 w-full mb-3" />
          <Skeleton className="h-6 w-full mb-3" />
          <Skeleton className="h-6 w-2/3" />
        </Card>
      ) : error ? (
        <Card>
          <EmptyState
            variant="no-data"
            title="Couldn't load configuration"
            description={
              error.status === 0
                ? 'The API is unreachable. Is the daemon running?'
                : `The health endpoint returned ${error.status}.`
            }
            action={{ label: 'Retry', onClick: reload }}
          />
        </Card>
      ) : health ? (
        <>
          <Section title="System" description="Current daemon status, reported live by /api/health.">
            <SystemSection health={health} />
          </Section>

          <Section
            title="Access token"
            description="Viewing is open on your LAN; applying a fix asks for this token. Reveal it to copy, or regenerate to rotate it (ARCHITECTURE §18.1)."
          >
            <AccessTokenSection />
          </Section>

          <Section
            title="Collection cadences"
            description="How often each collector job runs, and when it last succeeded."
          >
            <CadencesSection jobs={health.jobs} />
          </Section>

          <Section
            title="Controller & auth"
            description="Connection to the UniFi controller. Read-only; the controller host is host-only when exposed."
          >
            <PhasePlaceholder note="The read API does not expose controller connection details yet. This section fills in when the config endpoint ships; access stays read-only and the host is redacted to hostname only." />
          </Section>

          <Section
            title="Detector thresholds"
            description="The thresholds each detector evaluates against."
          >
            <PhasePlaceholder note="Thresholds in effect are not exposed by the read API yet. They surface here once the config endpoint ships." />
          </Section>

          <Section title="Home Assistant">
            <PhasePlaceholder note="Configured in data/config.yaml under the ha: block (enabled, broker host/port, credentials, discovery prefix). Not editable here." />
          </Section>

          <Section title="LLM provider">
            <PhasePlaceholder note="No global setting. Pick a provider (Manual, Copilot CLI, or Claude API) per investigation from the Investigation section on an issue's detail page. Claude API reads its key from the environment." />
          </Section>
        </>
      ) : null}
    </div>
  );
}
