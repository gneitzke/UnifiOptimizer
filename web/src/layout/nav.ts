import {
  Activity,
  Flame,
  History,
  FileText,
  LayoutDashboard,
  Laptop,
  Network,
  Settings,
  TriangleAlert,
  Wrench,
  type LucideIcon,
} from 'lucide-react';

/**
 * Sidebar destinations (docs/ARCHITECTURE.md §12 routes; docs §Interaction:
 * 5-9 flat destinations, one nesting level max). `badge: 'issues'` marks the
 * entry that carries the open-issue count.
 *
 * No standalone "Incidents" entry (Gitea #21): "incident" is a presentation
 * word for a genuine 2+ member group, and on a real capture that list has one
 * row while Issues has the other ten scattered underneath it — a second nav
 * destination for one row is not a destination. Issues is the one place
 * every open issue lives; a genuine incident renders inline there as a group
 * row, and `/incidents/:id` (the "whole story" page) is still reachable from
 * it and from the issue detail "Part of" line.
 */

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean; // exact match (for the index route)
  badge?: 'issues';
}

export const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/issues', label: 'Issues', icon: TriangleAlert, badge: 'issues' },
  { to: '/offenders', label: 'Offenders', icon: Flame },
  { to: '/devices', label: 'Devices', icon: Network },
  { to: '/clients', label: 'Clients', icon: Laptop },
  { to: '/timeline', label: 'Timeline', icon: Activity },
  { to: '/changes', label: 'Changes', icon: History },
  { to: '/visit', label: 'Visit', icon: Wrench },
  { to: '/report', label: 'Report', icon: FileText },
  { to: '/settings', label: 'Settings', icon: Settings },
];
