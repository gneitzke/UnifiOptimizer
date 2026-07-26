import type { TopologyModel } from '../model';
import { Section } from '../components/Section';
import { NoData } from '../components/NoData';
import { TopologyDiagram } from '../charts/TopologyDiagram';

/**
 * Section 5 — topology (docs/REPORT_SPEC.md §Topology): the layered diagram, with
 * mesh backhaul links coloured by health and labelled with RSSI.
 */
export function TopologySection({
  topology,
  site,
}: {
  topology: TopologyModel | null;
  site: string;
}) {
  const hasNodes = topology != null && topology.layers.some((l) => l.nodes.length > 0);
  return (
    <Section
      index={4}
      title="Topology"
      site={site}
      lead="How traffic reaches clients, from the WAN through the gateway and switches to the access points and their mesh backhaul."
    >
      {!hasNodes ? (
        <NoData label="Topology could not be reconstructed." reason="No uplink relationships were resolved for this site's devices." />
      ) : (
        <div className="flex flex-col gap-3">
          <div
            className="report-keep rounded-card p-4"
            style={{ border: '1px solid var(--hairline)' }}
          >
            <TopologyDiagram topology={topology} />
          </div>
          {topology.note && (
            <p className="t-caption" style={{ color: 'var(--fg-muted)', maxWidth: '44rem' }}>
              {topology.note}
            </p>
          )}
        </div>
      )}
    </Section>
  );
}
