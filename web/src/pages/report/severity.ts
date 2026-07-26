/**
 * CVSS severity presentation for the report (docs/REPORT_SPEC.md §Severity
 * colours) — one colour system end to end: the scorecard chips, the findings
 * table, and any per-entity status all resolve here.
 *
 * The five levels map onto the design foundation's AA-verified severity ramp
 * (docs/DESIGN_FOUNDATION.md §Color tokens), which is monotonic and re-picked per
 * theme, so both light and dark are first-class and the ramp stays
 * distinguishable:
 *
 *   critical → red     (--sev-p1)       light #D70015 / dark #FF6961
 *   high     → orange  (--sev-p2)       light #C93400 / dark #FFB340
 *   medium   → amber   (--sev-p3)       light #B25000 / dark #FFD426
 *   low      → green   (--sev-healthy)  light #1E7A34 / dark #30DB5B
 *   info     → grey    (--sev-neutral)  light #6E6E73 / dark #8E8E93
 *
 * This matches REPORT_SPEC's stated hexes for critical (#d70015), low (#1e7a34)
 * and info (grey), and its word "amber" for medium. REPORT_SPEC labels high as
 * "orange (#b25000)"; #b25000 is the ramp's amber (medium), so using it for high
 * would collide high and medium and break the ramp. High resolves to the truer
 * orange #C93400 the spec names, keeping the ramp readable and colour-blind-safe
 * (each level also carries a distinct shape glyph, never colour alone).
 */

import type { Band, ReportSeverity } from './model';

export const SEVERITY_ORDER: ReportSeverity[] = [
  'critical',
  'high',
  'medium',
  'low',
  'info',
];

export const SEVERITY_LABEL: Record<ReportSeverity, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  info: 'Info',
};

interface SeverityColors {
  /** AA text/icon colour on surface and canvas. */
  color: string;
  /** 10%/16% tint (per theme) for a chip background. */
  fill: string;
}

export const SEVERITY_COLORS: Record<ReportSeverity, SeverityColors> = {
  critical: { color: 'var(--sev-p1)', fill: 'var(--sev-p1-fill)' },
  high: { color: 'var(--sev-p2)', fill: 'var(--sev-p2-fill)' },
  medium: { color: 'var(--sev-p3)', fill: 'var(--sev-p3-fill)' },
  low: { color: 'var(--sev-healthy)', fill: 'var(--sev-healthy-fill)' },
  info: { color: 'var(--sev-neutral)', fill: 'var(--sev-neutral-fill)' },
};

/** Sort key, most severe first. */
export function severityRank(s: ReportSeverity): number {
  return SEVERITY_ORDER.indexOf(s);
}

/**
 * The band → colour for health scores, SLE bands, and topology backhaul. This is a
 * DISTINCT palette from the CVSS severity ramp above (`--health-*`, defined in
 * index.css / print.css): a degraded health bar must not render in the same red as
 * a High-severity chip, and each band keeps one meaning (green/amber/orange) across
 * light and dark, re-picked per theme for contrast — never swapping hue by theme.
 */
export const BAND_COLOR: Record<Band, string> = {
  good: 'var(--health-good)',
  fair: 'var(--health-fair)',
  poor: 'var(--health-poor)',
  none: 'var(--fg-subtle)',
};

export const BAND_WORD: Record<Band, string> = {
  good: 'Healthy',
  fair: 'Fair',
  poor: 'Degraded',
  none: 'No data',
};

/** Colour for a finding-evidence bar's optional status flag. */
export function statusColor(status: ReportSeverity | 'good' | null): string {
  if (status == null) return 'var(--accent)';
  if (status === 'good') return 'var(--sev-healthy)';
  return SEVERITY_COLORS[status].color;
}
