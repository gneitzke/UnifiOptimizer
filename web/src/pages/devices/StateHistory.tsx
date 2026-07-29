import { EmptyState, RelativeTime, cn } from '../../components/ui';
import { deviceStateLabel } from '../shared/eventVocab';
import type { StateChange } from './api';

/**
 * The discrete-state timeline for a device or client (docs §Devices/:id: "state
 * history", "firmware with state_changes history timeline"). Each recorded
 * transition — firmware upgrade, up/down flip, channel change, uplink change —
 * is a dot on a hairline rail, newest first. The first observation of an attr
 * (old_value null) reads as "set", later ones as "old → new" so a firmware bump
 * or a link flap is legible at a glance.
 */

const ATTR_LABEL: Record<string, string> = {
  firmware: 'Firmware',
  state: 'State',
  uplink_type: 'Uplink',
  channel: 'Channel',
  speed: 'Link speed',
  full_duplex: 'Duplex',
  up: 'Link',
  ap_mac: 'Access point',
  ip: 'IP address',
};

function attrLabel(attr: string): string {
  return ATTR_LABEL[attr] ?? attr;
}

/** Humanise a couple of raw state values so the rail reads cleanly. `attrLabel`
 * already prints the "State" noun, so the device-state value is just the word
 * ("offline"/"connected"), never the doubled "State state 0" (Gitea #52). */
function valueLabel(attr: string, value: string | null): string {
  if (value == null) return '—';
  if (attr === 'state') return deviceStateLabel(value);
  if (attr === 'up') return value === 'True' ? 'up' : 'down';
  if (attr === 'full_duplex') return value === 'True' ? 'full duplex' : 'half duplex';
  return value;
}

/** The raw stored value, surfaced on hover for the expert whenever we relabel a
 * device-state integer to a word. */
function rawTitle(attr: string, value: string | null): string | undefined {
  if (value == null || attr !== 'state') return undefined;
  return `state ${value}`;
}

export function StateHistory({
  changes,
  limit = 40,
}: {
  changes: StateChange[];
  limit?: number;
}) {
  if (changes.length === 0) {
    return (
      <EmptyState
        variant="no-data"
        title="No recorded changes"
        description="This entity's state has not changed since it was first seen."
      />
    );
  }

  const rows = changes.slice(0, limit);

  return (
    <ol className="flex flex-col m-0 p-0" style={{ listStyle: 'none' }}>
      {rows.map((c, i) => {
        const first = c.old_value == null;
        const last = i === rows.length - 1;
        return (
          <li key={c.id} className="flex gap-3">
            {/* Rail: dot + connector. */}
            <div className="flex flex-col items-center shrink-0" aria-hidden>
              <span
                className={cn('rounded-full mt-1.5')}
                style={{
                  width: 8,
                  height: 8,
                  background: first ? 'var(--fg-subtle)' : 'var(--accent)',
                }}
              />
              {!last && (
                <span
                  style={{ width: 1, flex: 1, minHeight: 20, background: 'var(--hairline)' }}
                />
              )}
            </div>

            <div className="pb-4 min-w-0">
              <div className="t-body" style={{ color: 'var(--fg)' }}>
                <span style={{ color: 'var(--fg-muted)' }}>{attrLabel(c.attr)}</span>{' '}
                {first ? (
                  <>
                    set to{' '}
                    <span
                      className="tnum"
                      style={{ color: 'var(--fg)' }}
                      title={rawTitle(c.attr, c.new_value)}
                    >
                      {valueLabel(c.attr, c.new_value)}
                    </span>
                  </>
                ) : (
                  <>
                    <span
                      className="tnum"
                      style={{ color: 'var(--fg-muted)' }}
                      title={rawTitle(c.attr, c.old_value)}
                    >
                      {valueLabel(c.attr, c.old_value)}
                    </span>
                    {' → '}
                    <span
                      className="tnum"
                      style={{ color: 'var(--fg)' }}
                      title={rawTitle(c.attr, c.new_value)}
                    >
                      {valueLabel(c.attr, c.new_value)}
                    </span>
                  </>
                )}
              </div>
              <div className="t-caption tnum" style={{ color: 'var(--fg-subtle)' }}>
                <RelativeTime ts={c.ts} mode="relative" />
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
