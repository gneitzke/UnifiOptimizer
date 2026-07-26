import {
  ArrowRightLeft,
  LogIn,
  LogOut,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';
import { EmptyState, RelativeTime, cn } from '../../components/ui';
import type { JourneyEvent } from '../devices/api';

/**
 * The client journey (docs §Clients/:id: "journey timeline — AP transitions from
 * events, disconnect markers with reason codes"). Salvaged from the old journey
 * expander, rebuilt on the design tokens: connect / roam / disconnect / anomaly
 * events on a hairline rail, newest first, each with the AP involved and a plain
 * one-line reason. Severity tint (amber) appears only on anomalies and negative
 * roams — the data itself being a problem (never-do rule 1). Nothing is
 * interpolated between events; the rail shows exactly what was recorded.
 */

interface Described {
  icon: LucideIcon;
  title: string;
  detail?: string;
  /** 'warn' = the event itself is a problem (anomaly / negative roam). */
  tone: 'neutral' | 'warn' | 'accent';
}

function humanDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${Math.floor(seconds)}s`;
}

function humanBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

const ANOMALY_LABEL: Record<string, string> = {
  USER_DNS_TIMEOUT: 'DNS timeout',
  USER_HIGH_DNS_LATENCY: 'High DNS latency',
  USER_HIGH_TCP_LATENCY: 'High TCP latency',
};

function describe(ev: JourneyEvent): Described {
  const d = ev.data as Record<string, unknown>;
  const ap = ev.related_entity?.name ?? (d.ap_name as string) ?? (d.ap_displayName as string);

  if (ev.key === 'EVT_WU_Connected') {
    const ch = d.channel;
    const w = d.channelWidth;
    const ssid = d.ssid as string | undefined;
    const bits = [
      ap ? `to ${ap}` : null,
      ch != null ? `ch ${ch}${w ? ` · ${w} MHz` : ''}` : null,
    ].filter(Boolean);
    return {
      icon: LogIn,
      title: ssid ? `Connected · ${ssid}` : 'Connected',
      detail: bits.join(' · ') || undefined,
      tone: 'accent',
    };
  }

  if (ev.key === 'EVT_WU_Disconnected' || ev.key === 'EVT_LU_Disconnected') {
    const ssid = d.ssid as string | undefined;
    const dur = humanDuration(Number(d.duration));
    const bytes = humanBytes(Number(d.bytes));
    const bits = [
      ap ? `from ${ap}` : null,
      dur ? `${dur} connected` : null,
      bytes ? `${bytes} transferred` : null,
    ].filter(Boolean);
    return {
      icon: LogOut,
      title: ssid ? `Disconnected · ${ssid}` : 'Disconnected',
      detail: bits.join(' · ') || undefined,
      tone: 'neutral',
    };
  }

  if (ev.key === 'EVT_WU_Roam') {
    const chFrom = d.channel_from;
    const chTo = d.channel_to;
    const negative = Boolean(d.is_negative);
    const bits = [
      ap ? `to ${ap}` : null,
      chFrom != null && chTo != null ? `ch ${chFrom} → ${chTo}` : null,
    ].filter(Boolean);
    return {
      icon: ArrowRightLeft,
      title: negative ? 'Roamed (suboptimal)' : 'Roamed',
      detail: bits.join(' · ') || undefined,
      tone: negative ? 'warn' : 'neutral',
    };
  }

  if (ev.key.startsWith('ANOMALY_')) {
    const name = (d.anomaly as string) ?? '';
    const label = ANOMALY_LABEL[name] ?? ev.msg ?? name.replace(/^USER_|^AP_/, '').replace(/_/g, ' ');
    return { icon: TriangleAlert, title: `Anomaly · ${label}`, tone: 'warn' };
  }

  // Unknown event — show it honestly rather than dropping it.
  return { icon: ArrowRightLeft, title: ev.msg ?? ev.key, tone: 'neutral' };
}

const TONE_COLOR: Record<Described['tone'], string> = {
  neutral: 'var(--fg-subtle)',
  accent: 'var(--accent)',
  warn: 'var(--sev-p2)',
};

export function JourneyTimeline({ events }: { events: JourneyEvent[] }) {
  if (events.length === 0) {
    return (
      <EmptyState
        variant="no-data"
        title="No journey events recorded"
        description="Connections, roams, and disconnects for this client will appear here as they are observed."
      />
    );
  }

  return (
    <ol className="flex flex-col m-0 p-0" style={{ listStyle: 'none' }}>
      {events.map((ev, i) => {
        const info = describe(ev);
        const Icon = info.icon;
        const last = i === events.length - 1;
        const color = TONE_COLOR[info.tone];
        return (
          <li key={ev.id} className="flex gap-3">
            <div className="flex flex-col items-center shrink-0" aria-hidden>
              <span
                className={cn('inline-flex items-center justify-center rounded-full')}
                style={{
                  width: 24,
                  height: 24,
                  color,
                  background:
                    info.tone === 'warn'
                      ? 'var(--sev-p2-fill)'
                      : info.tone === 'accent'
                        ? 'color-mix(in srgb, var(--accent) 12%, transparent)'
                        : 'var(--canvas)',
                  border: '1px solid var(--hairline)',
                }}
              >
                <Icon size={13} />
              </span>
              {!last && (
                <span
                  style={{ width: 1, flex: 1, minHeight: 16, background: 'var(--hairline)' }}
                />
              )}
            </div>

            <div className="pb-4 min-w-0">
              <div className="t-body" style={{ color: 'var(--fg)' }}>
                {info.title}
              </div>
              {info.detail && (
                <div className="t-secondary tnum" style={{ color: 'var(--fg-muted)' }}>
                  {info.detail}
                </div>
              )}
              <div className="t-caption tnum" style={{ color: 'var(--fg-subtle)' }}>
                <RelativeTime ts={ev.ts} mode="relative" />
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
