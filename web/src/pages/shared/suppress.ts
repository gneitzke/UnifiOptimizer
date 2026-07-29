/**
 * The suppression duration menu, shared by the per-issue control (issue detail)
 * and the bulk "Suppress incident" control (incident detail), so both offer the
 * exact same options and "Until I unsuppress" means the same thing on each
 * (Gitea #49/#50). `s: null` is the indefinite option (no `until_ts`); every
 * other value is added to `now` at click time to form the expiry.
 */
export const SUPPRESS_OPTIONS: { label: string; s: number | null }[] = [
  { label: '1 hour', s: 3_600 },
  { label: '8 hours', s: 28_800 },
  { label: '24 hours', s: 86_400 },
  { label: '3 days', s: 259_200 },
  { label: '7 days', s: 604_800 },
  { label: 'Until I unsuppress', s: null },
];
