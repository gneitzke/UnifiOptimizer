import type { Severity } from '../../api/types';
import { cn } from './cn';

/**
 * Severity presentation (docs §Severity & lifecycle presentation):
 * - Never color alone — every severity pairs a color with a SHAPE
 *   (P1 octagon, P2 triangle, P3 circle) so it survives colorblindness
 *   (never-do rule 2).
 * - Mid/low urgency = tinted text/icon on a neutral tint (10%/16% per theme).
 * - Solid fill is reserved for P1 only (the highest-urgency smallest element).
 */

const META: Record<Severity, { label: string; color: string; fill: string }> = {
  p1: { label: 'P1', color: 'var(--sev-p1)', fill: 'var(--sev-p1-fill)' },
  p2: { label: 'P2', color: 'var(--sev-p2)', fill: 'var(--sev-p2-fill)' },
  p3: { label: 'P3', color: 'var(--sev-p3)', fill: 'var(--sev-p3-fill)' },
};

/** The shape glyph alone (filled with currentColor). Reusable in table cells. */
export function SeverityGlyph({
  severity,
  size = 12,
  className,
}: {
  severity: Severity;
  size?: number;
  className?: string;
}) {
  const s = size;
  return (
    <svg
      width={s}
      height={s}
      viewBox="0 0 12 12"
      aria-hidden
      className={className}
      style={{ display: 'block', flexShrink: 0 }}
    >
      {severity === 'p1' && (
        <polygon
          points="3.5,0 8.5,0 12,3.5 12,8.5 8.5,12 3.5,12 0,8.5 0,3.5"
          fill="currentColor"
        />
      )}
      {severity === 'p2' && (
        <polygon points="6,0.5 11.7,11.2 0.3,11.2" fill="currentColor" />
      )}
      {severity === 'p3' && <circle cx="6" cy="6" r="4.6" fill="currentColor" />}
    </svg>
  );
}

interface Props {
  severity: Severity;
  /** Label text; defaults to the severity code (P1/P2/P3). */
  label?: string;
  /** Solid fill — honored for P1 only; other severities fall back to tint. */
  solid?: boolean;
  /** Hide the text and show only the shape glyph. */
  glyphOnly?: boolean;
  className?: string;
}

export function SeverityPill({
  severity,
  label,
  solid = false,
  glyphOnly = false,
  className,
}: Props) {
  const meta = META[severity];
  const isSolid = solid && severity === 'p1';
  const text = label ?? meta.label;

  if (glyphOnly) {
    return (
      <span
        title={text}
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
      {text}
    </span>
  );
}
