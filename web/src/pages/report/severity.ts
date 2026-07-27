/**
 * Severity presentation for the report — the SAME taxonomy and colours as the
 * app's `SeverityPill` (Gitea #22: these used to be two vocabularies, the
 * app's P1/P2/P3 and the report's own five-level scale re-derived from
 * measured impact, and they could disagree about the same finding). The
 * report's words are a fixed rename — P1→critical, P2→high, P3→low — done
 * once in the backend (`netadmin/report/severity.py`); `info` is reserved for
 * the one aggregated environmental finding. One colour system end to end: the
 * scorecard chips, the findings table, and any per-entity status all resolve
 * here.
 *
 * Colours are identical to `SeverityPill`'s P1/P2/P3 (red/orange/amber), re-
 * picked per theme per the design foundation's AA-verified severity ramp
 * (docs/DESIGN_FOUNDATION.md §Color tokens):
 *
 *   critical → red     (--sev-p1)       light #D70015 / dark #FF6961
 *   high     → orange  (--sev-p2)       light #C93400 / dark #FFB340
 *   low      → amber   (--sev-p3)       light #B25000 / dark #FFD426
 *   info     → grey    (--sev-neutral)  light #6E6E73 / dark #8E8E93
 *
 * Each level also carries a distinct shape glyph (octagon/triangle/circle/bar
 * — matching `SeverityPill`'s glyphs for P1/P2/P3), never colour alone.
 */

import type { Band, ReportSeverity } from './model';

export const SEVERITY_ORDER: ReportSeverity[] = ['critical', 'high', 'low', 'info'];

export const SEVERITY_LABEL: Record<ReportSeverity, string> = {
  critical: 'Critical',
  high: 'High',
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
  low: { color: 'var(--sev-p3)', fill: 'var(--sev-p3-fill)' },
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
