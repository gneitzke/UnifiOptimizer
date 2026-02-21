import {
  Routes,
  Route,
  Navigate,
} from 'react-router-dom';
import { useEffect } from 'react';
import { useAuthStore } from './stores/authStore';
import AppShell from
  './components/layout/AppShell';
import LoginPage from
  './components/auth/LoginPage';
import DashboardPage from
  './components/dashboard/DashboardPage';
import AnalysisPage from
  './components/analysis/AnalysisPage';
import HistoryPage from
  './components/history/HistoryPage';
import RepairPage from
  './components/repair/RepairPage';

/* ── Protected wrapper ─────────────────────────── */

function RequireAuth({
  children,
}: {
  children: React.ReactNode;
}) {
  const auth = useAuthStore(
    (s) => s.isAuthenticated,
  );
  const token = useAuthStore((s) => s.token);
  const isLoading = useAuthStore(
    (s) => s.isLoading,
  );

  // Token exists but not yet validated — wait
  if (token && !auth && isLoading) return null;

  if (!auth) return <Navigate to="/" replace />;
  return <>{children}</>;
}

/* ── Redirect to dashboard if already logged in ── */

function GuestOnly({
  children,
}: {
  children: React.ReactNode;
}) {
  const auth = useAuthStore(
    (s) => s.isAuthenticated,
  );
  if (auth) {
    return <Navigate to="/dashboard" replace />;
  }
  return <>{children}</>;
}

/* ── App ───────────────────────────────────────── */

export default function App() {
  const token = useAuthStore((s) => s.token);
  const validate = useAuthStore((s) => s.validate);
  const isAuthenticated = useAuthStore(
    (s) => s.isAuthenticated,
  );

  // Only validate on initial load when we have a stored
  // token but haven't confirmed the session yet.
  // Skip if already authenticated (just logged in).
  useEffect(() => {
    if (token && !isAuthenticated) void validate();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Routes>
      <Route
        path="/"
        element={
          <GuestOnly>
            <LoginPage />
          </GuestOnly>
        }
      />

      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route
          path="/dashboard"
          element={<DashboardPage />}
        />
        <Route
          path="/analysis/:id"
          element={<AnalysisPage />}
        />
        <Route
          path="/history"
          element={<HistoryPage />}
        />
        <Route
          path="/repair"
          element={<RepairPage />}
        />
      </Route>

      <Route
        path="*"
        element={
          <Navigate to="/" replace />
        }
      />
    </Routes>
  );
}
