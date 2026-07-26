import type { DeviceRow, InventoryModel } from '../model';
import { Section } from '../components/Section';
import { DocTable, type DocColumn } from '../components/DocTable';
import { NoData } from '../components/NoData';

/**
 * Section 4 — network inventory (docs/REPORT_SPEC.md §Network inventory): count
 * tiles for the fleet, then a device table (name, model, role, uplink).
 */
export function InventorySection({
  inventory,
  site,
}: {
  inventory: InventoryModel;
  site: string;
}) {
  const { counts, devices } = inventory;
  const tiles: { label: string; value: number | null }[] = [
    { label: 'Access points', value: counts.aps },
    { label: 'Switches', value: counts.switches },
    { label: 'Gateways', value: counts.gateways },
    { label: 'Clients', value: counts.clients },
  ];

  const columns: DocColumn<DeviceRow>[] = [
    { key: 'name', header: 'Device', render: (d) => <span style={{ fontWeight: 500 }}>{d.name}</span> },
    { key: 'model', header: 'Model', render: (d) => d.model ?? '—' },
    { key: 'role', header: 'Role', render: (d) => d.role },
    { key: 'uplink', header: 'Uplink', render: (d) => d.uplink ?? '—' },
  ];

  return (
    <Section index={3} title="Network inventory" site={site} lead="The managed fleet observed on this site during the window.">
      <div className="flex flex-col gap-6">
        <div className="report-keep grid grid-cols-2 sm:grid-cols-4 gap-3">
          {tiles.map((t) => (
            <div
              key={t.label}
              className="rounded-card p-4"
              style={{ border: '1px solid var(--hairline)' }}
            >
              <div className="t-metric tnum" style={{ color: 'var(--fg)' }}>
                {t.value == null ? '—' : t.value}
              </div>
              <div className="t-caption mt-0.5" style={{ color: 'var(--fg-muted)' }}>
                {t.label}
              </div>
            </div>
          ))}
        </div>

        <div>
          <h3 className="t-section mb-3" style={{ color: 'var(--fg)' }}>
            Devices
          </h3>
          {devices.length === 0 ? (
            <NoData label="No managed devices were resolved for this site." />
          ) : (
            <DocTable columns={columns} rows={devices} rowKey={(d) => d.id} />
          )}
        </div>
      </div>
    </Section>
  );
}
