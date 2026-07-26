import { useEffect, useRef, useState, type FormEvent, type ReactNode, type RefObject } from 'react';
import {
  AlertCircle,
  ArrowRight,
  Check,
  Copy,
  ExternalLink,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  PlayCircle,
  Radar,
  Server,
  ShieldCheck,
} from 'lucide-react';
import { Button } from '../../components/ui/Button';
import {
  connectController,
  detectConsole,
  scanForConsoles,
  SetupError,
  type SetupConnectBody,
  type SetupDetectResponse,
} from '../../api/setup';

/**
 * Build-time demo affordance. A demo build ships a throwaway token that
 * authenticates against a demo daemon; only then does "Explore a demo network"
 * become a real, working action. Absent the build flags it is not rendered at all
 * — never a button that goes nowhere.
 */
const DEMO_ENABLED = Boolean(import.meta.env.VITE_NETADMIN_DEMO);
const DEMO_TOKEN = (import.meta.env.VITE_NETADMIN_DEMO_TOKEN as string | undefined) ?? '';
const DEMO_AVAILABLE = DEMO_ENABLED && DEMO_TOKEN.length > 0;

/**
 * First-run controller connect (docs/ARCHITECTURE.md §18 / §18.1). A calm flow
 * that replaces the bare token gate on a fresh install and feels like it already
 * knows the network:
 *
 *   1. Connect — "Scan my network" runs a read-only LAN sweep and pre-fills the
 *      console address when it finds one (typing it stays the fallback); a
 *      read-only detect then shows the device-specific steps to create an API key
 *      (or, for an older/self-hosted controller, a local admin username/password).
 *      An "Explore a demo network" action appears when a demo build is served.
 *   2. Connected — under §18.1 viewing needs no token, so this is NOT a "save this
 *      or else" wall: it confirms the connect, says collection has started, and
 *      offers the access token as a quiet, dismissible note (you only need it to
 *      apply a fix). The user goes straight to the dashboard.
 *
 * Keyboard-first (autofocus walks host → credential → primary; Enter advances)
 * and DESIGN_FOUNDATION-compliant in both themes via the shared CSS tokens. The
 * UniFi key never leaves as a response; the UI token is returned exactly once.
 */

type Step = 'connect' | 'token';

/** Trim the parenthetical product code so the "Found" line reads like a name,
 *  e.g. "UniFi CloudKey Gen2 Plus (UCK-G2-Plus)" → "UniFi CloudKey Gen2 Plus". */
function cleanLabel(label: string): string {
  return label.replace(/\s*\([^)]*\)\s*$/, '').trim() || label;
}

function detectErrorCopy(err: SetupError, host: string): string {
  switch (err.kind) {
    case 'conflict':
      return 'This install is already set up. Reload the page to sign in with your access token.';
    case 'network':
      return "Couldn't reach UnifiOptimizer. Confirm the daemon is running, then try again.";
    case 'server':
      return 'The daemon hit an unexpected error. Check its logs, then try again.';
    default:
      return err.detail ?? `Couldn't check ${host}. Check the address and try again.`;
  }
}

function connectErrorCopy(err: SetupError, host: string): string {
  switch (err.kind) {
    case 'conflict':
      return 'This install is already set up. Reload the page to sign in with your access token.';
    case 'network':
      return "Couldn't reach UnifiOptimizer. Confirm the daemon is running, then try again.";
    case 'unreachable':
      return (
        err.detail ??
        `Couldn't reach the controller at ${host}. Check the address and that it's online.`
      );
    case 'server':
      return 'The daemon hit an unexpected error. Check its logs, then try again.';
    default:
      return (
        err.detail ??
        "That key wasn't accepted by the controller. Check that you copied the whole key, then try again."
      );
  }
}

export function SetupFlow({
  onAuthenticated,
}: {
  /** Called with the minted UI token once the user chooses to enter the dashboard. */
  onAuthenticated: (token: string) => void;
}) {
  const [step, setStep] = useState<Step>('connect');

  // Step 1 — detect
  const [host, setHost] = useState('');
  const [detecting, setDetecting] = useState(false);
  const [detection, setDetection] = useState<SetupDetectResponse | null>(null);
  // The host the current detection is for (the daemon doesn't echo it back, and
  // editing the field clears the detection, so this stays in sync with it).
  const [detectedHost, setDetectedHost] = useState('');
  const [hostError, setHostError] = useState<string | null>(null);

  // Step 1 — LAN scan assist
  const [scanning, setScanning] = useState(false);
  const [scanNote, setScanNote] = useState<string | null>(null);

  // Step 1 — credentials
  const [apiKey, setApiKey] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  // Manual override of the credential mode. The detect fingerprint recommends an
  // auth mode, but a user whose console can't mint an API key (older firmware, or
  // they just prefer a local admin) must be able to force the username/password
  // path — and back. `null` = follow the detected recommendation.
  const [credModeOverride, setCredModeOverride] = useState<'api_key' | 'cookie' | null>(null);

  // Step 2 — token
  const [uiToken, setUiToken] = useState('');
  const [copied, setCopied] = useState(false);
  const [copyHint, setCopyHint] = useState(false);

  const hostRef = useRef<HTMLInputElement>(null);
  const keyRef = useRef<HTMLInputElement>(null);
  const userRef = useRef<HTMLInputElement>(null);
  const tokenRef = useRef<HTMLElement>(null);

  const detectedAuthMode =
    detection?.playbook.auth_mode || detection?.console.recommended_auth || 'api_key';
  const detectedApiKey = detectedAuthMode === 'api_key';
  // The console can still take an API key when detect recommended one, or the
  // playbook says the model supports it — used to offer "use an API key instead".
  const apiKeyPossible = detectedApiKey || Boolean(detection?.playbook.supports_api_key);
  const isApiKey = credModeOverride ? credModeOverride === 'api_key' : detectedApiKey;

  // Focus walks the flow so the whole thing is drivable from the keyboard.
  useEffect(() => {
    if (step === 'connect' && !detection) hostRef.current?.focus();
  }, [step, detection]);
  useEffect(() => {
    if (!detection) return;
    (isApiKey ? keyRef : userRef).current?.focus();
    // Refocus when a *new* console is detected (keyed on the detected host) or the
    // user switches credential mode, so the newly-shown field takes focus.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detectedHost, credModeOverride]);

  const runDetect = async (raw: string) => {
    const h = raw.trim();
    if (!h || detecting) return;
    setDetecting(true);
    setHostError(null);
    setConnectError(null);
    try {
      const result = await detectConsole(h);
      if (!result.console.reachable || result.console.kind === 'unreachable') {
        setDetection(null);
        setHostError(
          `Nothing answered at ${h}. Check the address, and that a UniFi ` +
            'console (443) or legacy controller (8443) is reachable.',
        );
      } else {
        setApiKey('');
        setUsername('');
        setPassword('');
        setCredModeOverride(null); // a fresh detect follows the new recommendation
        setDetectedHost(h);
        setDetection(result);
      }
    } catch (err) {
      setDetection(null);
      setHostError(
        err instanceof SetupError ? detectErrorCopy(err, h) : 'Something went wrong. Try again.',
      );
    } finally {
      setDetecting(false);
    }
  };

  // Scan the daemon host's own LAN for a console and pre-fill the address. Typing
  // it stays the fallback; a found console flows straight into detect so the user
  // lands on the credential step without touching the keyboard.
  const runScan = async () => {
    if (scanning || detecting) return;
    setScanning(true);
    setScanNote(null);
    setHostError(null);
    try {
      const res = await scanForConsoles();
      const found = res.candidates;
      if (found.length > 0) {
        const first = found[0];
        setHost(first.host);
        const extra = found.length > 1 ? ` (+${found.length - 1} more found)` : '';
        setScanNote(`Found ${cleanLabel(first.label)}${extra}. Checking it now…`);
        await runDetect(first.host);
        setScanNote(null); // the detection result (or its error) now stands on its own
      } else if (res.scanned.length === 0) {
        setScanNote(
          "Couldn't read your local network from here. Enter the controller address above.",
        );
      } else {
        setScanNote(
          `No UniFi console answered on ${res.scanned.join(', ')}. Enter its address above.`,
        );
      }
    } catch (err) {
      setScanNote(
        err instanceof SetupError && err.detail
          ? err.detail
          : "Couldn't scan the network. Enter the controller address above.",
      );
    } finally {
      setScanning(false);
    }
  };

  const canConnect = isApiKey
    ? apiKey.trim().length > 0
    : username.trim().length > 0 && password.length > 0;

  const runConnect = async () => {
    if (!detection || !canConnect || connecting) return;
    const h = detectedHost || host.trim();
    const body: SetupConnectBody = isApiKey
      ? { host: h, api_key: apiKey.trim() }
      : { host: h, username: username.trim(), password };
    setConnecting(true);
    setConnectError(null);
    try {
      const res = await connectController(body);
      setUiToken(res.ui_token);
      setStep('token');
    } catch (err) {
      setConnectError(
        err instanceof SetupError ? connectErrorCopy(err, h) : 'Something went wrong. Try again.',
      );
    } finally {
      setConnecting(false);
    }
  };

  const copyToken = async () => {
    setCopyHint(false);
    let ok = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(uiToken);
        ok = true;
      }
    } catch {
      ok = false;
    }
    // Fallback for the daemon's plain-HTTP-over-LAN context, where the async
    // clipboard API is unavailable (it needs a secure origin).
    if (!ok && tokenRef.current) {
      const range = document.createRange();
      range.selectNodeContents(tokenRef.current);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
      try {
        ok = document.execCommand('copy');
      } catch {
        ok = false;
      }
    }
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } else {
      setCopyHint(true);
    }
  };

  const onHostSubmit = (e: FormEvent) => {
    e.preventDefault();
    void runDetect(host);
  };
  const onConnectSubmit = (e: FormEvent) => {
    e.preventDefault();
    void runConnect();
  };

  return (
    <div
      className="min-h-screen flex items-center justify-center px-6 py-10"
      style={{ background: 'var(--canvas)' }}
    >
      <div className="w-full max-w-[460px] flex flex-col">
        {/* Brand mark + step indicator */}
        <div className="flex items-center justify-between gap-4 mb-5">
          <span
            aria-hidden
            className="inline-flex items-center justify-center w-9 h-9 rounded-control t-label shrink-0"
            style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
          >
            UO
          </span>
          <span className="t-caption tnum" style={{ color: 'var(--fg-subtle)' }}>
            {step === 'connect' ? 'Step 1 of 2' : 'Connected'}
          </span>
        </div>

        {step === 'connect' ? (
          <ConnectStep
            host={host}
            setHost={(v) => {
              setHost(v);
              if (detection) setDetection(null);
              if (hostError) setHostError(null);
              if (scanNote) setScanNote(null);
            }}
            detecting={detecting}
            detection={detection}
            detectedHost={detectedHost}
            hostError={hostError}
            onHostSubmit={onHostSubmit}
            hostRef={hostRef}
            scanning={scanning}
            scanNote={scanNote}
            onScan={() => void runScan()}
            demoAvailable={DEMO_AVAILABLE}
            onDemo={() => onAuthenticated(DEMO_TOKEN)}
            isApiKey={isApiKey}
            apiKeyPossible={apiKeyPossible}
            onUsePassword={() => setCredModeOverride('cookie')}
            onUseApiKey={() => setCredModeOverride('api_key')}
            apiKey={apiKey}
            setApiKey={setApiKey}
            username={username}
            setUsername={setUsername}
            password={password}
            setPassword={setPassword}
            showSecret={showSecret}
            toggleShow={() => setShowSecret((s) => !s)}
            connecting={connecting}
            connectError={connectError}
            canConnect={canConnect}
            onConnectSubmit={onConnectSubmit}
            keyRef={keyRef}
            userRef={userRef}
          />
        ) : (
          <TokenStep
            token={uiToken}
            copied={copied}
            copyHint={copyHint}
            onCopy={copyToken}
            onEnter={() => onAuthenticated(uiToken)}
            tokenRef={tokenRef}
          />
        )}

        <p className="t-caption mt-4" style={{ color: 'var(--fg-subtle)' }}>
          {step === 'connect'
            ? 'Your API key is written only to the daemon, never shown here or sent to anyone.'
            : 'The access token is stored in this browser and sent as a bearer credential.'}
        </p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Step 1 — Connect                                                          */
/* ------------------------------------------------------------------------- */

function ConnectStep(props: {
  host: string;
  setHost: (v: string) => void;
  detecting: boolean;
  detection: SetupDetectResponse | null;
  detectedHost: string;
  hostError: string | null;
  onHostSubmit: (e: FormEvent) => void;
  hostRef: RefObject<HTMLInputElement | null>;
  scanning: boolean;
  scanNote: string | null;
  onScan: () => void;
  demoAvailable: boolean;
  onDemo: () => void;
  isApiKey: boolean;
  apiKeyPossible: boolean;
  onUsePassword: () => void;
  onUseApiKey: () => void;
  apiKey: string;
  setApiKey: (v: string) => void;
  username: string;
  setUsername: (v: string) => void;
  password: string;
  setPassword: (v: string) => void;
  showSecret: boolean;
  toggleShow: () => void;
  connecting: boolean;
  connectError: string | null;
  canConnect: boolean;
  onConnectSubmit: (e: FormEvent) => void;
  keyRef: RefObject<HTMLInputElement | null>;
  userRef: RefObject<HTMLInputElement | null>;
}) {
  const {
    host,
    setHost,
    detecting,
    detection,
    detectedHost,
    hostError,
    onHostSubmit,
    hostRef,
    scanning,
    scanNote,
    onScan,
    demoAvailable,
    onDemo,
    isApiKey,
    apiKeyPossible,
    onUsePassword,
    onUseApiKey,
    apiKey,
    setApiKey,
    username,
    setUsername,
    password,
    setPassword,
    showSecret,
    toggleShow,
    connecting,
    connectError,
    canConnect,
    onConnectSubmit,
    keyRef,
    userRef,
  } = props;

  const identified = Boolean(detection && detection.console.kind !== 'unknown_unifi_os');

  return (
    <>
      <div className="flex flex-col gap-1.5 mb-5">
        <h1 className="t-page-title" style={{ color: 'var(--fg)' }}>
          Connect your network
        </h1>
        <p className="t-body" style={{ color: 'var(--fg-muted)' }}>
          UnifiOptimizer watches your UniFi network, tracks issues over time, and proposes fixes you
          approve. Point it at your console to begin.
        </p>
      </div>

      <div
        className="rounded-card p-5 flex flex-col"
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--hairline)',
          boxShadow: 'var(--shadow-card)',
        }}
      >
        {/* Host detect */}
        <form onSubmit={onHostSubmit} className="flex flex-col">
          <div className="flex items-center justify-between gap-3 mb-1.5">
            <label htmlFor="setup-host" className="t-label" style={{ color: 'var(--fg)' }}>
              Controller address
            </label>
            <button
              type="button"
              onClick={onScan}
              disabled={scanning || detecting}
              className="inline-flex items-center gap-1.5 t-caption rounded-control transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
              style={{ color: 'var(--accent)' }}
            >
              {scanning ? (
                <>
                  <Loader2 size={13} className="animate-spin" aria-hidden /> Scanning…
                </>
              ) : (
                <>
                  <Radar size={13} aria-hidden /> Scan my network
                </>
              )}
            </button>
          </div>
          <input
            id="setup-host"
            ref={hostRef}
            type="text"
            inputMode="url"
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            value={host}
            onChange={(e) => setHost(e.target.value)}
            aria-invalid={Boolean(hostError)}
            aria-describedby="setup-host-help"
            placeholder="https://192.168.1.1"
            className="w-full h-9 px-3 rounded-control font-mono outline-none"
            style={{
              background: 'var(--canvas)',
              border: `1px solid ${hostError ? 'var(--sev-p1)' : 'var(--strong)'}`,
              color: 'var(--fg)',
              fontSize: 13,
            }}
          />
          <p id="setup-host-help" className="t-caption mt-2" style={{ color: 'var(--fg-subtle)' }}>
            The IP or URL of your UniFi console — or let UnifiOptimizer scan your network for it (a
            self-signed certificate is fine).
          </p>

          {scanNote && (
            <p
              className="t-caption mt-2 flex items-start gap-1.5"
              style={{ color: 'var(--fg-muted)' }}
              aria-live="polite"
            >
              <span aria-hidden className="mt-px shrink-0" style={{ color: 'var(--fg-subtle)' }}>
                <Radar size={13} />
              </span>
              <span>{scanNote}</span>
            </p>
          )}

          {hostError && <InlineError id="setup-host-error">{hostError}</InlineError>}

          <Button
            type="submit"
            variant={detection ? 'secondary' : 'primary'}
            size="md"
            className="w-full mt-4"
            disabled={host.trim().length === 0 || detecting}
          >
            {detecting ? (
              <>
                <Loader2 size={15} className="animate-spin" aria-hidden /> Detecting…
              </>
            ) : detection ? (
              'Re-check address'
            ) : (
              'Detect'
            )}
          </Button>
        </form>

        {/* Explore-a-demo. A demo build wires a real one-click button; a normal
            build shows an honest pointer to the CLI demo (no dead-end button). */}
        <div
          className="mt-4 pt-4 flex flex-col gap-2"
          style={{ borderTop: '1px solid var(--hairline)' }}
        >
          {demoAvailable ? (
            <>
              <Button
                type="button"
                variant="secondary"
                size="md"
                className="w-full"
                onClick={onDemo}
              >
                <PlayCircle size={15} aria-hidden />
                Explore a demo network
              </Button>
              <p className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
                No UniFi gear handy? Walk through a fully populated, fictional network.
              </p>
            </>
          ) : (
            <p className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
              Just want to look around first? Run{' '}
              <span className="font-mono" style={{ fontSize: 11.5 }}>
                netadmin demo-seed
              </span>{' '}
              to explore a fully populated, fictional network — no controller needed.
            </p>
          )}
        </div>

        {/* Detection result + credential entry */}
        {detection && (
          <div
            className="mt-5 pt-5 flex flex-col"
            style={{ borderTop: '1px solid var(--hairline)' }}
          >
            <div className="flex items-start gap-2.5">
              <span aria-hidden className="mt-0.5 shrink-0" style={{ color: 'var(--fg-muted)' }}>
                {identified ? <ShieldCheck size={18} /> : <Server size={18} />}
              </span>
              <div className="flex flex-col gap-1 min-w-0">
                <p className="t-label" style={{ color: 'var(--fg)' }}>
                  {identified
                    ? `Found: ${cleanLabel(detection.playbook.label)}`
                    : 'Reached a UniFi console'}
                </p>
                <p className="t-caption font-mono break-all" style={{ color: 'var(--fg-muted)' }}>
                  {detectedHost}
                </p>
                {!identified && (
                  <p className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
                    We couldn't read the exact model without signing in. That's fine, you can still
                    continue.
                  </p>
                )}
              </div>
            </div>

            {detection.playbook.steps.length > 0 && (
              <ol className="mt-4 flex flex-col gap-2">
                {detection.playbook.steps.map((s, i) => (
                  <li key={i} className="flex gap-2.5">
                    <span
                      aria-hidden
                      className="tnum t-caption shrink-0 mt-0.5 inline-flex items-center justify-center w-5 h-5 rounded-full"
                      style={{
                        background: 'var(--canvas)',
                        border: '1px solid var(--hairline)',
                        color: 'var(--fg-muted)',
                      }}
                    >
                      {i + 1}
                    </span>
                    <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
                      {s}
                    </span>
                  </li>
                ))}
              </ol>
            )}

            <a
              href={detection.console_url || detectedHost}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 t-label mt-4 self-start rounded-control transition-colors"
              style={{ color: 'var(--accent)' }}
            >
              Open my controller
              <ExternalLink size={14} aria-hidden />
            </a>

            <form
              onSubmit={onConnectSubmit}
              className="mt-4 pt-4 flex flex-col"
              style={{ borderTop: '1px solid var(--hairline)' }}
            >
              {isApiKey ? (
                <SecretField
                  id="setup-api-key"
                  label="Paste your API key"
                  toggleName="API key"
                  value={apiKey}
                  onChange={setApiKey}
                  show={showSecret}
                  toggleShow={toggleShow}
                  inputRef={keyRef}
                  invalid={Boolean(connectError)}
                  placeholder="Paste the key you just created"
                />
              ) : (
                <div className="flex flex-col gap-3">
                  <div className="flex flex-col">
                    <label
                      htmlFor="setup-username"
                      className="t-label mb-1.5"
                      style={{ color: 'var(--fg)' }}
                    >
                      Local admin username
                    </label>
                    <input
                      id="setup-username"
                      ref={userRef}
                      type="text"
                      autoComplete="off"
                      autoCorrect="off"
                      autoCapitalize="off"
                      spellCheck={false}
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      aria-invalid={Boolean(connectError)}
                      className="w-full h-9 px-3 rounded-control outline-none"
                      style={{
                        background: 'var(--canvas)',
                        border: `1px solid ${connectError ? 'var(--sev-p1)' : 'var(--strong)'}`,
                        color: 'var(--fg)',
                        fontSize: 13,
                      }}
                    />
                  </div>
                  <SecretField
                    id="setup-password"
                    label="Password"
                    toggleName="password"
                    value={password}
                    onChange={setPassword}
                    show={showSecret}
                    toggleShow={toggleShow}
                    invalid={Boolean(connectError)}
                  />
                </div>
              )}

              {/* Credential-mode fallback. An API key is the modern path, but an
                  older or self-hosted controller may have none — this makes the
                  username/password route obvious, and reversible. */}
              {isApiKey ? (
                <button
                  type="button"
                  onClick={onUsePassword}
                  className="inline-flex items-center gap-1.5 t-caption mt-3 self-start rounded-control transition-colors cursor-pointer"
                  style={{ color: 'var(--accent)' }}
                >
                  <KeyRound size={13} aria-hidden />
                  My controller has no API key (older or self-hosted)
                </button>
              ) : (
                apiKeyPossible && (
                  <button
                    type="button"
                    onClick={onUseApiKey}
                    className="inline-flex items-center gap-1.5 t-caption mt-3 self-start rounded-control transition-colors cursor-pointer"
                    style={{ color: 'var(--accent)' }}
                  >
                    <KeyRound size={13} aria-hidden />
                    Use an API key instead
                  </button>
                )
              )}

              {connectError && <InlineError id="setup-connect-error">{connectError}</InlineError>}

              <Button
                type="submit"
                variant="primary"
                size="md"
                className="w-full mt-4"
                disabled={!canConnect || connecting}
              >
                {connecting ? (
                  <>
                    <Loader2 size={15} className="animate-spin" aria-hidden /> Connecting…
                  </>
                ) : (
                  'Connect'
                )}
              </Button>
            </form>
          </div>
        )}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------------- */
/* Step 2 — Connected (collecting; the token is a quiet, dismissible note)     */
/* ------------------------------------------------------------------------- */

function TokenStep({
  token,
  copied,
  copyHint,
  onCopy,
  onEnter,
  tokenRef,
}: {
  token: string;
  copied: boolean;
  copyHint: boolean;
  onCopy: () => void;
  onEnter: () => void;
  tokenRef: RefObject<HTMLElement | null>;
}) {
  // The token is genuinely optional to view here: §18.1 makes viewing open, so the
  // dashboard just loads. The token only gates a fix, so the note is dismissible
  // and never blocks the primary action.
  const [noteOpen, setNoteOpen] = useState(true);

  return (
    <>
      <div className="flex items-start gap-2.5 mb-5">
        <span aria-hidden className="mt-0.5 shrink-0" style={{ color: 'var(--sev-healthy)' }}>
          <ShieldCheck size={22} />
        </span>
        <div className="flex flex-col gap-1.5">
          <h1 className="t-page-title" style={{ color: 'var(--fg)' }}>
            You're connected
          </h1>
          <p className="t-body" style={{ color: 'var(--fg-muted)' }}>
            UnifiOptimizer is reading your controller now. Your first results appear on the
            dashboard in a few minutes.
          </p>
        </div>
      </div>

      <Button
        type="button"
        variant="primary"
        size="md"
        className="w-full"
        onClick={onEnter}
        autoFocus
      >
        Go to the dashboard
        <ArrowRight size={15} aria-hidden />
      </Button>

      {/* Quiet, dismissible access-token note — not a wall. This browser is already
          signed in; the token is only needed to apply a fix from another device. */}
      {noteOpen && (
        <div
          className="mt-4 rounded-card p-4 flex flex-col"
          style={{
            background: 'var(--surface)',
            border: '1px solid var(--hairline)',
          }}
        >
          <div className="flex items-center justify-between gap-3 mb-1.5">
            <span className="t-label" style={{ color: 'var(--fg)' }}>
              Access token
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={onCopy}
                className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-control t-caption cursor-pointer transition-colors"
                style={{
                  background: 'var(--canvas)',
                  border: '1px solid var(--strong)',
                  color: copied ? 'var(--sev-healthy)' : 'var(--fg-muted)',
                }}
                aria-live="polite"
              >
                {copied ? (
                  <>
                    <Check size={13} aria-hidden /> Copied
                  </>
                ) : (
                  <>
                    <Copy size={13} aria-hidden /> Copy
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={() => setNoteOpen(false)}
                className="inline-flex items-center h-7 px-2.5 rounded-control t-caption cursor-pointer transition-colors"
                style={{ color: 'var(--fg-subtle)' }}
              >
                Dismiss
              </button>
            </div>
          </div>

          <code
            ref={tokenRef}
            className="block w-full rounded-control p-2.5 font-mono break-all select-all"
            style={{
              background: 'var(--canvas)',
              border: '1px solid var(--hairline)',
              color: 'var(--fg)',
              fontSize: 13,
              lineHeight: '20px',
            }}
          >
            {token}
          </code>

          {copyHint && (
            <p className="t-caption mt-2" style={{ color: 'var(--fg-subtle)' }}>
              Couldn't copy automatically. Select the token above and copy it manually.
            </p>
          )}

          <p className="t-caption mt-2" style={{ color: 'var(--fg-subtle)' }}>
            You'll only need this to apply a fix. This browser is already signed in; copy it now if
            you'll sign in from another device.
          </p>
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------------------- */
/* Shared bits                                                               */
/* ------------------------------------------------------------------------- */

function SecretField({
  id,
  label,
  toggleName,
  value,
  onChange,
  show,
  toggleShow,
  inputRef,
  invalid,
  placeholder,
}: {
  id: string;
  label: string;
  /** Short noun for the show/hide button's a11y label (e.g. "API key"), kept
   *  distinct from the field label so it doesn't collide under getByLabel. */
  toggleName: string;
  value: string;
  onChange: (v: string) => void;
  show: boolean;
  toggleShow: () => void;
  inputRef?: RefObject<HTMLInputElement | null>;
  invalid?: boolean;
  placeholder?: string;
}) {
  return (
    <div className="flex flex-col">
      <label htmlFor={id} className="t-label mb-1.5" style={{ color: 'var(--fg)' }}>
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          ref={inputRef}
          type={show ? 'text' : 'password'}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-invalid={Boolean(invalid)}
          placeholder={placeholder}
          className="w-full h-9 pl-3 pr-10 rounded-control font-mono outline-none"
          style={{
            background: 'var(--canvas)',
            border: `1px solid ${invalid ? 'var(--sev-p1)' : 'var(--strong)'}`,
            color: 'var(--fg)',
            fontSize: 13,
          }}
        />
        <button
          type="button"
          onClick={toggleShow}
          aria-label={show ? `Hide ${toggleName}` : `Show ${toggleName}`}
          aria-pressed={show}
          className="absolute right-1 top-1 inline-flex items-center justify-center w-7 h-7 rounded-control cursor-pointer transition-colors"
          style={{ color: 'var(--fg-subtle)' }}
        >
          {show ? <EyeOff size={15} /> : <Eye size={15} />}
        </button>
      </div>
    </div>
  );
}

function InlineError({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p
      id={id}
      role="alert"
      className="t-caption mt-2 flex items-start gap-1.5"
      style={{ color: 'var(--sev-p1)' }}
    >
      <span aria-hidden className="mt-px shrink-0">
        <AlertCircle size={13} />
      </span>
      <span>{children}</span>
    </p>
  );
}
