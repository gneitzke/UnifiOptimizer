import { NavLink } from 'react-router-dom';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { NAV_ITEMS } from './nav';
import { CountBadge } from '../components/ui/CountBadge';
import { cn } from '../components/ui/cn';
import type { IssueSummary } from '../api/hooks';
import type { InstallMethod, UpdateVariant } from '../api/update';

/**
 * Flat, collapsible sidebar (docs §Interaction: 5-9 flat destinations, quiet
 * gray count badges, red only when P1s exist, collapsible to icons). The active
 * item is marked with the accent as a tint + text, never a full severity/solid
 * fill.
 */

interface Props {
  expanded: boolean;
  onToggle: () => void;
  summary?: IssueSummary;
  /** From `/api/health` (`build_health`'s `version`). Undefined while loading. */
  version?: string;
  /** From `/api/system/update`, for the version footer's hover tooltip. */
  installMethod?: InstallMethod;
  installVariant?: UpdateVariant;
}

const INSTALL_METHOD_LABEL: Record<InstallMethod, string> = {
  pip: 'Installed via pip',
  container: 'Installed in a container',
  addon: 'Installed as a Home Assistant add-on',
  source: 'Running from a source checkout',
};

const INSTALL_VARIANT_LABEL: Record<'compose' | 'macmini', string> = {
  compose: 'Docker Compose',
  macmini: 'Mac mini deploy',
};

function installTitle(method?: InstallMethod, variant?: UpdateVariant): string | undefined {
  if (!method) return undefined;
  const base = INSTALL_METHOD_LABEL[method];
  return variant ? `${base} (${INSTALL_VARIANT_LABEL[variant]})` : base;
}

export function Sidebar({ expanded, onToggle, summary, version, installMethod, installVariant }: Props) {
  return (
    <aside
      className="no-print flex flex-col shrink-0 transition-[width] duration-200"
      style={{
        width: expanded ? 220 : 60,
        borderRight: '1px solid var(--hairline)',
        background: 'var(--surface)',
      }}
    >
      <div
        className={cn('flex items-center h-14 px-3', expanded ? 'gap-2' : 'justify-center')}
      >
        <span
          aria-hidden
          className="inline-flex items-center justify-center w-7 h-7 rounded-control shrink-0 t-label"
          style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
        >
          UO
        </span>
        {expanded && (
          <span className="t-section" style={{ color: 'var(--fg)' }}>
            UnifiOptimizer
          </span>
        )}
      </div>

      <nav className="flex-1 flex flex-col gap-0.5 px-2 mt-1">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const count = item.badge === 'issues' ? (summary?.open ?? 0) : 0;
          const alert = item.badge === 'issues' ? Boolean(summary?.hasP1) : false;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              title={!expanded ? item.label : undefined}
              className={({ isActive }) =>
                cn(
                  'group relative flex items-center h-9 rounded-control transition-colors',
                  expanded ? 'px-2.5 gap-3' : 'justify-center',
                  isActive ? 'font-medium' : '',
                )
              }
              style={({ isActive }) => ({
                background: isActive
                  ? 'color-mix(in srgb, var(--accent) 12%, transparent)'
                  : 'transparent',
                color: isActive ? 'var(--accent)' : 'var(--fg-muted)',
              })}
            >
              <span className="relative inline-flex shrink-0">
                <Icon size={18} />
                {!expanded && count > 0 && (
                  <span
                    aria-hidden
                    className="absolute -top-1 -right-1 w-2 h-2 rounded-full"
                    style={{
                      background: alert ? 'var(--sev-p1)' : 'var(--fg-subtle)',
                      outline: '2px solid var(--surface)',
                    }}
                  />
                )}
              </span>
              {expanded && (
                <>
                  <span className="flex-1 t-body">{item.label}</span>
                  {item.badge === 'issues' && (
                    <CountBadge
                      count={count}
                      alert={alert}
                      title={
                        item.badge === 'issues' && summary && summary.suppressed > 0
                          ? `${count} open issues · ${summary.suppressed} suppressed`
                          : `${count} open issues`
                      }
                    />
                  )}
                </>
              )}
            </NavLink>
          );
        })}
      </nav>

      {expanded && version && (
        <div
          className="px-3 py-2 t-caption truncate"
          style={{ borderTop: '1px solid var(--hairline)', color: 'var(--fg-subtle)' }}
          title={installTitle(installMethod, installVariant)}
        >
          v{version}
        </div>
      )}

      <button
        type="button"
        onClick={onToggle}
        aria-label={expanded ? 'Collapse sidebar' : 'Expand sidebar'}
        className="flex items-center justify-center h-10 m-2 rounded-control cursor-pointer transition-colors hover:bg-canvas"
        style={{ color: 'var(--fg-subtle)' }}
      >
        {expanded ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
      </button>
    </aside>
  );
}
