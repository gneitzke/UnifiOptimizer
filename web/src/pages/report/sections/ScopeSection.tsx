import type { ReportMeta, ScopeModel } from '../model';
import { Section } from '../components/Section';
import { NoData } from '../components/NoData';
import { fmtDateRange } from '../format';

/**
 * Section 3 — scope & methodology (docs/REPORT_SPEC.md §Scope & methodology).
 * What was assessed, the data sources, the sampling cadence, the collection
 * window, and the honest limitations, up front so the reader can weigh the rest.
 */
export function ScopeSection({
  scope,
  meta,
  site,
}: {
  scope: ScopeModel;
  meta: ReportMeta;
  site: string;
}) {
  return (
    <Section
      index={2}
      title="Scope & methodology"
      site={site}
      lead="How this assessment was gathered, and the limits of what it can and cannot see."
    >
      <div className="flex flex-col gap-6">
        <Block title="Data sources">
          {scope.data_sources.length === 0 ? (
            <NoData label="No data sources recorded." />
          ) : (
            <BulletList items={scope.data_sources} />
          )}
        </Block>

        <Block title="Collection window & sampling">
          <p className="t-secondary mb-3" style={{ color: 'var(--fg-muted)' }}>
            {fmtDateRange(meta.window.start_ts, meta.window.end_ts)} · {meta.window.label}.
          </p>
          {scope.sampling.length === 0 ? (
            <NoData label="Sampling cadence not recorded." />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full t-secondary" style={{ borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ color: 'var(--fg-muted)', borderBottom: '1px solid var(--hairline)' }}>
                    <th className="text-left font-medium py-2 pr-4">Source</th>
                    <th className="text-left font-medium py-2">Cadence</th>
                  </tr>
                </thead>
                <tbody>
                  {scope.sampling.map((row, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--hairline)' }}>
                      <td className="py-2 pr-4" style={{ color: 'var(--fg)' }}>
                        {row.source}
                      </td>
                      <td className="py-2 tnum" style={{ color: 'var(--fg-muted)' }}>
                        {row.cadence}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Block>

        <Block title="Poll coverage">
          <p className="t-secondary mb-3" style={{ color: 'var(--fg-muted)' }}>
            The share of expected polls that actually landed. A partial window is
            reported plainly, never smoothed to look complete.
          </p>
          {scope.coverage.length === 0 ? (
            <NoData label="No coverage was measured for this window." />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full t-secondary" style={{ borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ color: 'var(--fg-muted)', borderBottom: '1px solid var(--hairline)' }}>
                    <th className="text-left font-medium py-2 pr-4">Job</th>
                    <th className="text-left font-medium py-2 pr-4">Interval</th>
                    <th className="text-right font-medium py-2 pr-4">Coverage</th>
                    <th className="text-left font-medium py-2">Note</th>
                  </tr>
                </thead>
                <tbody>
                  {scope.coverage.map((row, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--hairline)' }}>
                      <td className="py-2 pr-4" style={{ color: 'var(--fg)' }}>
                        {row.job}
                      </td>
                      <td className="py-2 pr-4 tnum" style={{ color: 'var(--fg-muted)' }}>
                        {row.interval}
                      </td>
                      <td className="py-2 pr-4 text-right tnum" style={{ color: 'var(--fg)' }}>
                        {Math.round(row.fraction * 100)}%
                      </td>
                      <td className="py-2" style={{ color: 'var(--fg-muted)' }}>
                        {row.note}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Block>

        <Block title="Limitations">
          {scope.limitations.length === 0 ? (
            <NoData label="No limitations recorded." reason="Absence here is unusual. Treat scope as complete only if the collection window covered normal use." />
          ) : (
            <BulletList items={scope.limitations} muted />
          )}
        </Block>
      </div>
    </Section>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="t-label mb-2" style={{ color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {title}
      </h3>
      {children}
    </div>
  );
}

function BulletList({ items, muted }: { items: string[]; muted?: boolean }) {
  return (
    <ul className="flex flex-col gap-1.5" style={{ margin: 0, padding: 0, listStyle: 'none' }}>
      {items.map((it, i) => (
        <li key={i} className="flex gap-2 t-body" style={{ color: muted ? 'var(--fg-muted)' : 'var(--fg)' }}>
          <span aria-hidden style={{ color: 'var(--fg-subtle)', lineHeight: '20px' }}>
            ·
          </span>
          <span style={{ maxWidth: '44rem' }}>{it}</span>
        </li>
      ))}
    </ul>
  );
}
