import type { ClientsModel, WorstDevice } from '../model';
import { Section } from '../components/Section';
import { NoData } from '../components/NoData';
import { Histogram } from '../charts/Histogram';
import { CategoryBars, type CategoryBar } from '../charts/CategoryBars';
import { DocTable, type DocColumn } from '../components/DocTable';

/**
 * Section 8 — client analysis (docs/REPORT_SPEC.md §Client analysis): the RSSI
 * distribution (weak tail coloured — a mean would hide it), clients-per-AP load
 * bars, and the worst-devices table.
 */
export function ClientsSection({
  clients,
  site,
}: {
  clients: ClientsModel;
  site: string;
}) {
  const loadBars: CategoryBar[] = clients.per_ap_load.map((a) => ({
    label: a.ap_name,
    value: a.client_count,
  }));

  const columns: DocColumn<WorstDevice>[] = [
    { key: 'name', header: 'Device', render: (d) => <span style={{ fontWeight: 500 }}>{d.name}</span> },
    {
      key: 'metrics',
      header: 'Measured',
      render: (d) => (
        <span className="flex flex-wrap gap-x-4 gap-y-0.5">
          {d.metrics.map((m, i) => (
            <span key={i} className="tnum" style={{ color: 'var(--fg-muted)' }}>
              <span style={{ color: 'var(--fg-subtle)' }}>{m.label} </span>
              {m.value}
            </span>
          ))}
        </span>
      ),
    },
  ];

  return (
    <Section
      index={7}
      title="Client analysis"
      site={site}
      lead="How clients experience the network: the spread of signal strength, how load sits across access points, and the devices having the worst time."
    >
      <div className="flex flex-col gap-6">
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-card p-4" style={{ border: '1px solid var(--hairline)' }}>
            <Histogram
              bins={clients.rssi_histogram}
              contextLabel="Client signal strength distribution"
              summaryStat={
                clients.total_clients != null ? `${clients.total_clients} clients` : undefined
              }
              takeaway="The weak tail is where roaming stalls and throughput collapses. A mean would hide it."
            />
          </div>

          <div className="rounded-card p-4" style={{ border: '1px solid var(--hairline)' }}>
            {loadBars.length === 0 ? (
              <NoData label="No per-AP client load was resolved." />
            ) : (
              <CategoryBars
                data={loadBars}
                orientation="horizontal"
                contextLabel="Clients per access point"
                unit="clients"
                takeaway="Uneven load points to a coverage gap or a band-steering imbalance."
              />
            )}
          </div>
        </div>

        <div>
          <h3 className="t-section mb-3" style={{ color: 'var(--fg)' }}>
            Devices having the worst experience
          </h3>
          {clients.worst_devices.length === 0 ? (
            <NoData label="No client stood out as a persistent offender." reason="No device crossed the worst-experience thresholds during the window." />
          ) : (
            <DocTable columns={columns} rows={clients.worst_devices} rowKey={(_, i) => i} />
          )}
        </div>
      </div>
    </Section>
  );
}
