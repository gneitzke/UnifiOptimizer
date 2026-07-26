import { useEffect, useState, type ReactNode } from 'react';
import { RefreshCw } from 'lucide-react';
import { SetupFlow } from '../pages/onboarding/SetupFlow';
import { Button } from '../components/ui/Button';
import { getSetupStatus, type SetupStatus } from './setup';
import { setToken } from './token';

/**
 * First-run gate (docs/ARCHITECTURE.md §18 / §18.1).
 *
 * §18.1 "already set up = just works": reads are open on the LAN once configured,
 * so there is NO returning-user token wall for viewing. This gate's only job is the
 * first-run branch — it reads the daemon's setup state once and either runs the
 * SetupFlow (fresh install) or renders the app (configured):
 *
 *   `GET /api/setup/status`
 *     • `configured: false` (fresh install) → the multi-step SetupFlow, which
 *       connects the controller and mints the access token.
 *     • `configured: true`                  → straight into the dashboard. No token
 *       is needed to view; GET reads are open.
 *
 * The access token is only ever prompted just-in-time, by `TokenPrompt` (mounted at
 * the app root), when a *mutating* action is attempted without a valid token —
 * never here, and never for viewing.
 */

type Phase =
  | { kind: 'loading' }
  | { kind: 'error' }
  | { kind: 'ready'; status: SetupStatus };

export function TokenGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getSetupStatus()
      .then((status) => {
        if (!cancelled) setPhase({ kind: 'ready', status });
      })
      .catch(() => {
        if (!cancelled) setPhase({ kind: 'error' });
      });
    return () => {
      cancelled = true;
    };
  }, [nonce]);

  const retry = () => {
    setPhase({ kind: 'loading' });
    setNonce((n) => n + 1);
  };

  // Setup finished: persist the minted token for this browser and treat the daemon
  // as configured, so the app renders without a second round-trip.
  const onConfigured = (token: string) => {
    setToken(token);
    setPhase({ kind: 'ready', status: { configured: true, controller_connected: true } });
  };

  if (phase.kind === 'loading') {
    return <GateLoading />;
  }
  if (phase.kind === 'error') {
    return <GateUnreachable onRetry={retry} />;
  }
  if (!phase.status.configured) {
    return <SetupFlow onAuthenticated={onConfigured} />;
  }
  return <>{children}</>;
}

/** Brief neutral hold while the one status read resolves — no fake content. */
function GateLoading() {
  return (
    <div
      className="min-h-screen flex items-center justify-center"
      style={{ background: 'var(--canvas)' }}
      aria-busy="true"
    >
      <span className="sr-only">Loading UnifiOptimizer…</span>
    </div>
  );
}

/** The daemon itself is unreachable — honest, with a retry rather than a guess. */
function GateUnreachable({ onRetry }: { onRetry: () => void }) {
  return (
    <div
      className="min-h-screen flex items-center justify-center px-6 py-10"
      style={{ background: 'var(--canvas)' }}
    >
      <div className="w-full max-w-[400px] flex flex-col">
        <span
          aria-hidden
          className="inline-flex items-center justify-center w-9 h-9 rounded-control t-label shrink-0 mb-5"
          style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
        >
          UO
        </span>
        <div
          className="rounded-card p-5 flex flex-col"
          style={{
            background: 'var(--surface)',
            border: '1px solid var(--hairline)',
            boxShadow: 'var(--shadow-card)',
          }}
        >
          <h1 className="t-section" style={{ color: 'var(--fg)' }}>
            Can't reach UnifiOptimizer
          </h1>
          <p className="t-body mt-1.5" style={{ color: 'var(--fg-muted)' }}>
            The daemon didn't answer. Confirm it's running and reachable from this browser, then
            try again.
          </p>
          <Button variant="secondary" size="md" className="mt-4 self-start" onClick={onRetry}>
            <RefreshCw size={15} aria-hidden />
            Try again
          </Button>
        </div>
      </div>
    </div>
  );
}
