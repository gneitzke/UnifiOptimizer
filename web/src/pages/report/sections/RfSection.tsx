import type { RfModel } from '../model';
import { Section } from '../components/Section';
import { NoData } from '../components/NoData';
import { CategoryBars, type CategoryBar } from '../charts/CategoryBars';

/**
 * Section 7 — RF environment (docs/REPORT_SPEC.md §RF environment): channel
 * utilisation per band against a fixed reference line, and neighbour-AP density by
 * channel as aggregated environmental context — never a per-BSSID alarm list. A
 * dense-RF site is framed honestly as context, not as a wall of issues.
 */
export function RfSection({ rf, site }: { rf: RfModel; site: string }) {
  const bands = uniqueBands(rf.utilization.map((u) => u.band));
  const hasUtil = rf.utilization.some((u) => u.utilization_pct != null);
  const hasNeighbors = rf.neighbors.some((n) => n.count != null);
  const ref =
    rf.reference_pct != null
      ? { value: rf.reference_pct, label: `${rf.reference_pct}% congested` }
      : undefined;

  const neighborBars: CategoryBar[] = rf.neighbors.map((n) => ({
    label: `${n.band}·${n.channel}`,
    value: n.count,
  }));

  return (
    <Section
      index={6}
      title="RF environment"
      site={site}
      lead="How busy the air is on each channel, and how much of that is neighbouring networks you don't control."
    >
      <div className="flex flex-col gap-6">
        {rf.summary && (
          <p className="t-body" style={{ color: 'var(--fg)', maxWidth: '44rem' }}>
            {rf.summary}
          </p>
        )}

        <div>
          <h3 className="t-section mb-3" style={{ color: 'var(--fg)' }}>
            Channel utilisation by band
          </h3>
          {!hasUtil ? (
            <NoData label="No channel-utilisation samples in this window." reason="Utilisation needs radio airtime telemetry, which this controller did not report." />
          ) : (
            <div className="flex flex-col gap-5">
              {bands.map((band) => {
                const rows = rf.utilization.filter(
                  (u) => u.band === band && u.utilization_pct != null,
                );
                // One bar per radio, labelled by its AP and channel so bars are never
                // ambiguous (two APs can sit on the same channel). Values shown, with
                // the band's peak as the summary stat.
                const bars: CategoryBar[] = rows.map((u) => ({
                  label: `${u.ap_name} · ch ${u.channel}`,
                  value: u.utilization_pct,
                }));
                const peak = rows.reduce((m, u) => Math.max(m, u.utilization_pct ?? 0), 0);
                return (
                  <div
                    key={band}
                    className="rounded-card p-4"
                    style={{ border: '1px solid var(--hairline)' }}
                  >
                    <CategoryBars
                      data={bars}
                      orientation="horizontal"
                      percentage
                      reference={ref}
                      unit="%"
                      contextLabel={`${band} GHz airtime by radio`}
                      summaryStat={`Peak ${Math.round(peak)}%`}
                      takeaway={`Bars past the ${rf.reference_pct ?? 70}% line are where airtime is scarce and latency climbs.`}
                    />
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div>
          <h3 className="t-section mb-3" style={{ color: 'var(--fg)' }}>
            Neighbour density by channel
          </h3>
          {!hasNeighbors ? (
            <NoData label="No neighbouring networks were catalogued." />
          ) : (
            <div
              className="rounded-card p-4"
              style={{ border: '1px solid var(--hairline)' }}
            >
              <CategoryBars
                data={neighborBars}
                contextLabel="Neighbouring access points seen per channel"
                unit="APs"
                height={190}
                takeaway={
                  rf.rogue_count != null
                    ? `Aggregated environmental context, not per-network issues. ${rf.rogue_count} appeared on your own SSIDs and warrant a look.`
                    : 'Aggregated environmental context: neighbouring networks are noise you route around, not faults to fix.'
                }
              />
            </div>
          )}
        </div>
      </div>
    </Section>
  );
}

function uniqueBands(bands: string[]): string[] {
  const seen: string[] = [];
  for (const b of bands) if (!seen.includes(b)) seen.push(b);
  return seen;
}
