import { useCallback, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Command, Moon, Sun } from 'lucide-react';
import { Sidebar } from './Sidebar';
import { CommandPalette } from './CommandPalette';
import { NAV_ITEMS } from './nav';
import { CommandPaletteProvider } from './keyboard/useCommandPalette';
import { useCommandPalette } from './keyboard/commandPaletteContext';
import { FilterFocusProvider } from './keyboard/useSlashFocus';
import { useFocusFilter } from './keyboard/filterFocusContext';
import { useHotkeys } from './keyboard/useHotkeys';
import { useTheme } from '../theme';
import { useHealth, useIssueSummary } from '../api/hooks';
import { WsProvider, useWsStatus, useWsFrames } from '../api/WsProvider';
import { cn } from '../components/ui/cn';
import { UpdateBanner, useUpdateStatus } from './update';

/**
 * Application shell (docs/ARCHITECTURE.md §12; docs §Interaction). Owns the
 * sidebar, the top bar, the command palette, and the global keyboard model:
 * Cmd+K (palette) and `/` (focus the current view's filter). Live issue counts
 * feed the sidebar badges and are nudged by WebSocket issue transitions.
 */

function currentTitle(pathname: string): string {
  // Longest matching destination wins (so /issues/42 → "Issues").
  const match = NAV_ITEMS.filter((n) =>
    n.end ? pathname === n.to : pathname === n.to || pathname.startsWith(`${n.to}/`),
  ).sort((a, b) => b.to.length - a.to.length)[0];
  return match?.label ?? 'UnifiOptimizer';
}

function ShellInner() {
  const location = useLocation();
  const { theme, toggle } = useTheme();
  const { toggle: togglePalette } = useCommandPalette();
  const focusFilter = useFocusFilter();
  const { summary, reload } = useIssueSummary();
  // No polling: the sidebar's version footer only needs to be right on load,
  // since it changes at most once per self-upgrade, which already restarts
  // the daemon.
  const health = useHealth(0);
  // Own poll, independent of `UpdateBanner`'s below, purely to source the
  // footer's install-method tooltip.
  const { status: updateStatus } = useUpdateStatus();

  const [expanded, setExpanded] = useState(() => {
    try {
      return localStorage.getItem('netadmin_sidebar') !== 'collapsed';
    } catch {
      return true;
    }
  });

  const toggleSidebar = useCallback(() => {
    setExpanded((v) => {
      const next = !v;
      try {
        localStorage.setItem('netadmin_sidebar', next ? 'expanded' : 'collapsed');
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  // `/` focuses the active view's filter; only swallow the key if one exists.
  useHotkeys([
    {
      combo: '/',
      preventDefault: false,
      handler: (e) => {
        if (focusFilter()) e.preventDefault();
      },
    },
  ]);

  const wsStatus = useWsStatus();
  useWsFrames((frame) => {
    if (frame.type === 'issue_transition') reload();
  });

  const wsState =
    wsStatus === 'open'
      ? { label: 'Live', color: 'var(--sev-healthy)' }
      : wsStatus === 'connecting'
        ? { label: 'Connecting', color: 'var(--sev-p3)' }
        : { label: 'Offline', color: 'var(--fg-subtle)' };

  return (
    <div className="flex flex-col min-h-screen" style={{ background: 'var(--canvas)' }}>
      <UpdateBanner />

      <div className="flex flex-1 min-h-0">
        <Sidebar
          expanded={expanded}
          onToggle={toggleSidebar}
          summary={summary}
          version={health.data?.version}
          installMethod={updateStatus?.install_method}
          installVariant={updateStatus?.variant}
        />

        <div className="flex-1 flex flex-col min-w-0">
          <header
            className="no-print flex items-center justify-between h-14 px-6 shrink-0"
            style={{ borderBottom: '1px solid var(--hairline)', background: 'var(--surface)' }}
          >
            <h1 className="t-section" style={{ color: 'var(--fg)' }}>
              {currentTitle(location.pathname)}
            </h1>

            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1.5 t-caption mr-1" style={{ color: 'var(--fg-muted)' }}>
                <span
                  aria-hidden
                  className="inline-block w-2 h-2 rounded-full"
                  style={{ background: wsState.color }}
                />
                {wsState.label}
              </span>

              <button
                type="button"
                onClick={togglePalette}
                className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-control t-caption cursor-pointer transition-colors hover:bg-canvas"
                style={{ border: '1px solid var(--hairline)', color: 'var(--fg-muted)' }}
                aria-label="Open command palette"
              >
                <Command size={13} />
                <span className="font-mono">K</span>
              </button>

              <button
                type="button"
                onClick={toggle}
                aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
                className={cn(
                  'inline-flex items-center justify-center w-8 h-8 rounded-control cursor-pointer transition-colors hover:bg-canvas',
                )}
                style={{ color: 'var(--fg-muted)' }}
              >
                {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
              </button>
            </div>
          </header>

          <main className="flex-1 min-w-0 overflow-y-auto print:overflow-visible">
            <Outlet />
          </main>
        </div>

        <CommandPalette />
      </div>
    </div>
  );
}

export default function AppShell() {
  return (
    <WsProvider>
      <CommandPaletteProvider>
        <FilterFocusProvider>
          <ShellInner />
        </FilterFocusProvider>
      </CommandPaletteProvider>
    </WsProvider>
  );
}
