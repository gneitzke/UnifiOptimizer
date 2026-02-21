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
  if (!auth) return <Navigate to="/" replace />;
  return <>{children}</>;
}

/* ── App ───────────────────────────────────────── */

export default function App() {
  const { token, validate } = useAuthStore();

  useEffect(() => {
    if (token) void validate();
  }, [token, validate]);

  return (
    <Routes>
      <Route path="/" element={<LoginPage />} />

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
