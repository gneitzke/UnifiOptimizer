import { Navigate, Route, Routes } from 'react-router-dom';
import { TokenGate } from './api/TokenGate';
import { TokenPrompt } from './api/TokenPrompt';
import AppShell from './layout/AppShell';
import { DashboardPage } from './pages/dashboard';
import { IssueDetailPage, IssuesPage } from './pages/issues';
import { IncidentDetailPage, IncidentsPage } from './pages/incidents';
import { OffendersPage } from './pages/offenders';
import { DevicesPage, DeviceDetailPage } from './pages/devices';
import { ClientsPage, ClientDetailPage } from './pages/clients';
import { TimelinePage } from './pages/timeline';
import { ChangesPage } from './pages/changes';
import { VisitPage } from './pages/visit';
import { SettingsPage } from './pages/settings';
import { ReportPage } from './pages/report';
import { GuidedTour } from './pages/onboarding';

/**
 * Router shell. AppShell is the persistent layout; every destination in
 * docs/ARCHITECTURE.md §12 has a route here. The pages themselves are honest
 * stubs at this stage — the page agents build them next from the shared UI
 * primitives (components/ui) and the API client (src/api).
 */

export default function App() {
  return (
    <>
      <TokenGate>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<DashboardPage />} />
            <Route path="issues" element={<IssuesPage />} />
            <Route path="issues/:id" element={<IssueDetailPage />} />
            <Route path="incidents" element={<IncidentsPage />} />
            <Route path="incidents/:id" element={<IncidentDetailPage />} />
            <Route path="offenders" element={<OffendersPage />} />
            <Route path="devices" element={<DevicesPage />} />
            <Route path="devices/:id" element={<DeviceDetailPage />} />
            <Route path="clients" element={<ClientsPage />} />
            <Route path="clients/:id" element={<ClientDetailPage />} />
            <Route path="timeline" element={<TimelinePage />} />
            <Route path="changes" element={<ChangesPage />} />
            <Route path="visit" element={<VisitPage />} />
            <Route path="report" element={<ReportPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>

        {/* First-run guided tour. Inside the gate so it runs only once the app is
            shown; it self-starts once, and is re-runnable from Settings. */}
        <GuidedTour />
      </TokenGate>

      {/* Just-in-time access-token prompt (§18.1). Mounted at the root, outside the
          gate, so any mutating action can raise it over the live dashboard without
          ever blocking viewing. */}
      <TokenPrompt />
    </>
  );
}
