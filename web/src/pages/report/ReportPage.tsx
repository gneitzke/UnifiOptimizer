import { Printer } from 'lucide-react';
import './print.css';
import { Button } from '../../components/ui/Button';
import { Skeleton } from '../../components/ui/Skeleton';
import { usePageAsync } from '../shared/hooks';
import { getReport } from './api';
import { PRINT_HINT, printReport } from './exportReport';
import { fmtDateTime } from './format';
import { CoverSection } from './sections/CoverSection';
import { ExecutiveSummary } from './sections/ExecutiveSummary';
import { ScopeSection } from './sections/ScopeSection';
import { InventorySection } from './sections/InventorySection';
import { TopologySection } from './sections/TopologySection';
import { HealthSection } from './sections/HealthSection';
import { RfSection } from './sections/RfSection';
import { ClientsSection } from './sections/ClientsSection';
import { FindingsSection } from './sections/FindingsSection';
import { RecommendationsSection } from './sections/RecommendationsSection';
import { AppendixSection } from './sections/AppendixSection';

/**
 * `/report` — the print-optimised network assessment (docs/ARCHITECTURE.md §19;
 * docs/REPORT_SPEC.md). Fetches the whole report model from `GET /api/report` and
 * renders it in the spec's section order. The page renders the model AS GIVEN and
 * computes no number of its own; a missing field shows an honest "no data" state.
 * The Export action triggers the browser's Save-as-PDF (no server PDF engine).
 */
export default function ReportPage() {
  const { data, error, loading } = usePageAsync(getReport, []);

  if (loading && !data) return <LoadingState />;
  if (error && !data) return <ErrorState message={describeError(error.status)} />;
  if (!data) return <ErrorState message="The report is empty." />;

  const m = data;
  const site = m.meta.site_name;

  return (
    <div className="report-root" style={{ background: 'var(--canvas)' }}>
      <Toolbar generatedTs={m.meta.generated_ts} />

      <CoverSection meta={m.meta} />
      <ExecutiveSummary exec={m.executive} site={site} />
      <ScopeSection scope={m.scope} meta={m.meta} site={site} />
      <InventorySection inventory={m.inventory} site={site} />
      <TopologySection topology={m.topology} site={site} />
      <HealthSection health={m.health} site={site} />
      <RfSection rf={m.rf} site={site} />
      <ClientsSection clients={m.clients} site={site} />
      <FindingsSection findings={m.findings} site={site} />
      <RecommendationsSection recommendations={m.recommendations} site={site} />
      <AppendixSection appendix={m.appendix} site={site} />
    </div>
  );
}

function Toolbar({ generatedTs }: { generatedTs: number }) {
  return (
    <div
      className="no-print flex flex-wrap items-center justify-between gap-3 mb-6 pb-4"
      style={{ borderBottom: '1px solid var(--hairline)' }}
    >
      <div>
        <div className="t-label" style={{ color: 'var(--fg-muted)' }}>
          Assessment report
        </div>
        <div className="t-caption tnum" style={{ color: 'var(--fg-subtle)' }}>
          Generated {fmtDateTime(generatedTs)}
        </div>
      </div>
      <div className="flex flex-col items-end gap-1">
        <Button variant="primary" onClick={printReport}>
          <Printer size={16} aria-hidden />
          Export / Save as PDF
        </Button>
        <span className="t-micro" style={{ color: 'var(--fg-subtle)', maxWidth: '22rem', textAlign: 'right' }}>
          {PRINT_HINT}
        </span>
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="report-root">
      <div className="flex flex-col gap-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="report-root">
      <div
        className="rounded-card p-6"
        style={{ border: '1px solid var(--hairline)' }}
      >
        <h2 className="t-section" style={{ color: 'var(--fg)' }}>
          The report is not available
        </h2>
        <p className="t-secondary mt-1" style={{ color: 'var(--fg-muted)', maxWidth: '38rem' }}>
          {message}
        </p>
      </div>
    </div>
  );
}

function describeError(status: number): string {
  if (status === 0) return 'The daemon could not be reached. Check that it is running and try again.';
  if (status === 404)
    return 'The report endpoint is not available on this daemon yet. It appears once the report assembler is deployed.';
  if (status === 503)
    return 'The daemon is still starting and has not assembled a report yet. Try again in a moment.';
  return `The report request failed (HTTP ${status}).`;
}
