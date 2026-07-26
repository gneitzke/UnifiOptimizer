/**
 * Tour steps as data (in-app-help pattern). Each step optionally navigates to a
 * route, then spotlights the first target selector that resolves on that page.
 * Targets are tried in order and fall back to a centred popover when none match,
 * so a still-loading or restructured page degrades gracefully instead of pointing
 * at nothing. The copy stands on its own and reads correctly whichever anchor
 * (feature element, then nav item) the runner lands on.
 *
 * Each step points at ONE representative element, not a whole page region — a
 * spotlight that rings the entire content area points at nothing. So: the
 * Network-health card (its own `data-tour` anchor, not the section that also holds
 * the Collectors strip the copy never mentions) for step 1; the first table row
 * for the issues and devices steps (`table[role="grid"]` is our own DataTable
 * primitive, stable to target). The always-present sidebar destinations (stable
 * react-router hrefs) remain the resilient fallback when a page is still loading,
 * empty, or restructured.
 */

export interface TourStep {
  id: string;
  /** Route to be on for this step; the runner navigates if not already there. */
  route: string;
  /** Ordered candidate selectors; first visible match is spotlit. */
  targets: string[];
  title: string;
  body: string;
}

export const TOUR_STEPS: TourStep[] = [
  {
    id: 'health-verdict',
    route: '/',
    targets: ['[data-tour="network-health"]', 'a[href="/"]'],
    title: "Your network's health at a glance",
    body: 'Your overall health lives here — one score from 0 to 100, weighted across the service levels, with 24 hours of trend behind it once data has collected. A lower score points you to what is dragging it down.',
  },
  {
    id: 'sle-why',
    route: '/',
    targets: ['main > div > section:nth-of-type(2)', 'a[href="/"]'],
    title: "See what's dragging a score down",
    body: 'Each service level has its own score and trend. Choose "Why" on any card to expand the failing classifiers and the devices or clients behind them.',
  },
  {
    id: 'issues-list',
    route: '/issues',
    targets: ['table[role="grid"] tbody tr', 'a[href="/issues"]'],
    title: 'Every issue, tracked over time',
    body: 'Problems UnifiOptimizer finds land here and stay tracked, with severity, how long they have been open, and the evidence that opened them. Nothing resets between visits.',
  },
  {
    id: 'device-drilldown',
    route: '/devices',
    targets: ['table[role="grid"] tbody tr', 'a[href="/devices"]'],
    title: 'Drill into any device',
    body: 'Open a device for its ports, radios, and metric history, plus the issues attributed to it. That is the whole tour. Replay it anytime from Settings.',
  },
];
