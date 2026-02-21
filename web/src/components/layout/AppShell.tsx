import { useState } from 'react';
import {
  Outlet,
  NavLink,
  useLocation,
} from 'react-router-dom';
import {
  LayoutDashboard,
  Activity,
  History,
  Wrench,
  ChevronLeft,
  ChevronRight,
  Sun,
  Moon,
  LogOut,
} from 'lucide-react';
import { useThemeStore } from '../../stores/themeStore';
import { useAuthStore } from '../../stores/authStore';

const NAV = [
  {
    to: '/dashboard',
    icon: LayoutDashboard,
    label: 'Dashboard',
  },
  {
    to: '/analysis/new',
    icon: Activity,
    label: 'Analysis',
  },
  {
    to: '/history',
    icon: History,
    label: 'History',
  },
  {
    to: '/repair',
    icon: Wrench,
    label: 'Repair',
  },
] as const;

export default function AppShell() {
  const [expanded, setExpanded] = useState(false);
  const { theme, toggleTheme } = useThemeStore();
  const { username, host, logout } = useAuthStore();
  const location = useLocation();

  const crumb = NAV.find((n) =>
    location.pathname.startsWith(n.to),
  );

  const w = expanded
    ? 'var(--sidebar-w-expanded)'
    : 'var(--sidebar-w)';

  return (
    <div
      className="flex min-h-screen"
      style={{ background: 'var(--bg)' }}
    >
      {/* ── Sidebar ───────────────────────── */}
      <aside
        className="flex flex-col shrink-0
          transition-all duration-200"
        style={{
          width: w,
          borderRight:
            '1px solid var(--border)',
          background: 'var(--bg-surface)',
        }}
      >
        {/* Logo */}
        <div
          className="flex items-center justify-center
            h-14 font-bold tracking-wide"
          style={{ color: 'var(--primary)' }}
        >
          {expanded ? 'UniFi Opt' : 'U'}
        </div>

        {/* Nav links */}
        <nav className="flex-1 flex flex-col gap-1
          px-2 mt-2"
        >
          {NAV.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2
                 rounded-lg transition-colors
                 text-sm font-medium
                 ${isActive
                  ? 'text-white'
                  : 'hover:opacity-80'
                }`
              }
              style={({ isActive }) => ({
                background: isActive
                  ? 'var(--primary)'
                  : 'transparent',
                color: isActive
                  ? '#fff'
                  : 'var(--text-muted)',
              })}
            >
              <Icon size={20} />
              {expanded && (
                <span>{label}</span>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Collapse toggle */}
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center justify-center
            h-10 opacity-60 hover:opacity-100
            transition-opacity cursor-pointer"
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--text-muted)',
          }}
          aria-label="Toggle sidebar"
        >
          {expanded
            ? <ChevronLeft size={18} />
            : <ChevronRight size={18} />}
        </button>
      </aside>

      {/* ── Main area ─────────────────────── */}
      <div className="flex-1 flex flex-col
        min-h-screen overflow-hidden"
      >
        {/* Top bar */}
        <header
          className="flex items-center
            justify-between h-14 px-6 shrink-0"
          style={{
            borderBottom:
              '1px solid var(--border)',
            background: 'var(--bg-surface)',
          }}
        >
          {/* Breadcrumb */}
          <span
            className="text-sm font-medium"
            style={{ color: 'var(--text-muted)' }}
          >
            {crumb?.label ?? 'Dashboard'}
          </span>

          {/* Right controls */}
          <div className="flex items-center gap-4">
            <button
              onClick={toggleTheme}
              className="p-1.5 rounded-lg
                cursor-pointer transition-colors"
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--text-muted)',
              }}
              aria-label="Toggle theme"
            >
              {theme === 'dark'
                ? <Sun size={18} />
                : <Moon size={18} />}
            </button>

            <span
              className="text-xs"
              style={{ color: 'var(--text-muted)' }}
            >
              {username}
              {host && (
                <span className="opacity-50">
                  {' '}@ {host}
                </span>
              )}
            </span>

            <button
              onClick={() => void logout()}
              className="p-1.5 rounded-lg
                cursor-pointer transition-colors"
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--text-muted)',
              }}
              aria-label="Logout"
            >
              <LogOut size={18} />
            </button>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
