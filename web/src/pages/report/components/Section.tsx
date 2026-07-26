import type { ReactNode } from 'react';
import { cn } from '../../../components/ui/cn';

/**
 * A report section: a print page-break boundary (via `.report-section`, see
 * print.css) carrying a print-only running header (site + Confidential + section
 * name) and a numbered heading. Sections read as the chapters of a document.
 */

interface SectionProps {
  /** Section ordinal shown before the title, e.g. "3". Omit for the cover. */
  index?: number;
  title: string;
  /** Site name for the print running header. */
  site: string;
  /** A one-line orienting subtitle under the heading. */
  lead?: ReactNode;
  cover?: boolean;
  children: ReactNode;
  className?: string;
}

export function Section({
  index,
  title,
  site,
  lead,
  cover = false,
  children,
  className,
}: SectionProps) {
  return (
    <section
      className={cn(
        'report-section report-page',
        cover && 'report-section--cover',
        className,
      )}
      aria-label={title}
    >
      {/* Print-only running header — repeats the site + confidentiality at the top
          of each section (i.e. most printed pages) for Chromium, which cannot
          paint the @page margin boxes. */}
      <div className="report-runhead" aria-hidden>
        <span>{site}</span>
        <span>{title} · Confidential</span>
      </div>

      {!cover && (
        <header className="mb-5">
          <h2 className="t-page-title" style={{ color: 'var(--fg)' }}>
            {index != null && (
              <span className="tnum" style={{ color: 'var(--fg-subtle)', marginRight: 12 }}>
                {String(index).padStart(2, '0')}
              </span>
            )}
            {title}
          </h2>
          {lead && (
            <p
              className="t-secondary mt-1"
              style={{ color: 'var(--fg-muted)', maxWidth: '46rem' }}
            >
              {lead}
            </p>
          )}
        </header>
      )}

      {children}
    </section>
  );
}
