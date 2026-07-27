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
  /** Print-only: glue the heading to its content as one unbreakable unit, so the
   *  heading is never stranded alone at the top of a page while the content
   *  bumps to the next (Gitea #27). Opt-in and meant ONLY for a section whose
   *  entire content is a single large figure (e.g. Topology) — most sections
   *  have several independently-flowable chunks (chart cards, tiles, finding
   *  entries) that must keep flowing across pages on their own, or the whole
   *  section would jump as one glob and strand the PREVIOUS page half-empty
   *  instead (see print.css's note on large sub-blocks). */
  keepHeadingWithContent?: boolean;
}

export function Section({
  index,
  title,
  site,
  lead,
  cover = false,
  children,
  className,
  keepHeadingWithContent = false,
}: SectionProps) {
  const body = (
    <>
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
    </>
  );

  const runhead = (
    <div className="report-runhead" aria-hidden>
      <span>{site}</span>
      <span>{title} · Confidential</span>
    </div>
  );

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
          paint the @page margin boxes. Bundled INTO the same atomic group as the
          heading below when `keepHeadingWithContent` is set: left outside, its
          few tenths of an inch are just enough to tip an otherwise-fitting
          heading+figure group past the page bottom, stranding the runhead alone
          on the first page and pushing heading AND figure together to the next
          (Gitea #27) — worse than the orphan this prop exists to fix. */}
      {keepHeadingWithContent ? (
        <div className="report-keep">
          {runhead}
          {body}
        </div>
      ) : (
        <>
          {runhead}
          {body}
        </>
      )}
    </section>
  );
}
