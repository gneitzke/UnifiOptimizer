import type { ReportSeverity } from '../model';
import { SEVERITY_COLORS, SEVERITY_LABEL } from '../severity';
import { cn } from '../../../components/ui/cn';

/**
 * CVSS severity chip (docs/REPORT_SPEC.md §Severity colours) — the one severity
 * presentation used in the scorecard and the findings table. Colour never stands
 * alone: each level carries a distinct shape glyph so it survives colour-blindness
 * and greyscale printing (docs/DESIGN_FOUNDATION.md never-do rule 2).
 */

/** The shape alone, filled with currentColor. Five distinct silhouettes. */
export function SeverityGlyph({
  severity,
  size = 11,
  className,
}: {
  severity: ReportSeverity;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 12 12"
      aria-hidden
      className={className}
      style={{ display: 'block', flexShrink: 0 }}
    >
      {severity === 'critical' && (
        <polygon
          points="3.5,0 8.5,0 12,3.5 12,8.5 8.5,12 3.5,12 0,8.5 0,3.5"
          fill="currentColor"
        />
      )}
      {severity === 'high' && (
        <polygon points="6,0.5 11.7,11.2 0.3,11.2" fill="currentColor" />
      )}
      {severity === 'medium' && (
        <polygon points="6,0 12,6 6,12 0,6" fill="currentColor" />
      )}
      {severity === 'low' && <circle cx="6" cy="6" r="4.6" fill="currentColor" />}
      {severity === 'info' && (
        <rect x="1.5" y="5" width="9" height="2" rx="1" fill="currentColor" />
      )}
    </svg>
  );
}

interface Props {
  severity: ReportSeverity;
  /** Show the shape only (for dense table cells). */
  glyphOnly?: boolean;
  /** Solid fill — reserved for critical only, else a tint. */
  solid?: boolean;
  className?: string;
}

export function SeverityChip({ severity, glyphOnly, solid, className }: Props) {
  const meta = SEVERITY_COLORS[severity];
  const label = SEVERITY_LABEL[severity];
  const isSolid = solid && severity === 'critical';

  if (glyphOnly) {
    return (
      <span
        title={label}
        className={cn('inline-flex', className)}
        style={{ color: meta.color, lineHeight: 0 }}
      >
        <SeverityGlyph severity={severity} />
      </span>
    );
  }

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 h-[20px] px-1.5 rounded-full',
        'text-[12px] font-medium whitespace-nowrap',
        className,
      )}
      style={{
        background: isSolid ? meta.color : meta.fill,
        color: isSolid ? '#ffffff' : meta.color,
      }}
    >
      <SeverityGlyph severity={severity} size={10} />
      {label}
    </span>
  );
}
