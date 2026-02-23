import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Wifi,
  Server,
  Loader2,
  AlertCircle,
  ArrowLeft,
} from 'lucide-react';
import { useAuthStore } from '../../stores/authStore';
import { useNavigate } from 'react-router-dom';
import { discover } from '../../services/api';
import type { DiscoveredDevice } from '../../types/api';

type Mode =
  | 'choose'
  | 'manual'
  | 'scan'
  | 'credentials';

export default function LoginPage() {
  const { login, isLoading } = useAuthStore();
  const navigate = useNavigate();

  const [mode, setMode] = useState<Mode>('choose');
  const [host, setHost] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState('');
  const [scanning, setScanning] = useState(false);
  const [devices, setDevices] = useState<
    DiscoveredDevice[]
  >([]);

  async function handleScan() {
    setError('');
    setScanning(true);
    setMode('scan');
    try {
      const found = await discover();
      setDevices(found);
    } catch (err) {
      const msg =
        err instanceof Error
          ? err.message
          : String(err);
      setError(msg);
    } finally {
      setScanning(false);
    }
  }

  async function handleSubmit(
    e: React.FormEvent,
  ) {
    e.preventDefault();
    setError('');
    try {
      await login({
        host,
        username,
        password,
        remember,
      });
      navigate('/analysis/new');
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : String(err);
      setError(msg);
    }
  }

  /* ── Background gradient ─────────────────── */
  const bg = {
    background:
      'linear-gradient(135deg,'
      + ' #0d1117 0%,'
      + ' #151b28 40%,'
      + ' #1a2540 100%)',
  };

  const card: React.CSSProperties = {
    background: 'var(--glass-bg)',
    backdropFilter: 'blur(16px)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
  };

  const inputStyle: React.CSSProperties = {
    background: 'var(--bg)',
    border: '1px solid var(--border-strong)',
    borderRadius: '8px',
    color: 'var(--text)',
    padding: '10px 14px',
    width: '100%',
    fontSize: '14px',
    outline: 'none',
  };

  return (
    <div
      className="flex items-center justify-center
        min-h-screen"
      style={bg}
    >
      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-sm p-8"
        style={card}
      >
        {/* Header */}
        <div className="text-center mb-8">
          <div
            className="inline-flex items-center
              justify-center w-14 h-14 rounded-2xl
              mb-4"
            style={{
              background:
                'rgba(0,136,255,0.12)',
            }}
          >
            <Wifi
              size={28}
              style={{ color: 'var(--primary)' }}
            />
          </div>
          <h1
            className="text-xl font-bold"
            style={{ color: 'var(--text)' }}
          >
            UniFi Optimizer
          </h1>
          <p
            className="text-sm mt-1"
            style={{
              color: 'var(--text-muted)',
            }}
          >
            Connect to your controller
          </p>
        </div>

        <AnimatePresence mode="wait">
          {/* ── Choose mode ──────────────── */}
          {mode === 'choose' && (
            <motion.div
              key="choose"
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 20 }}
              className="flex flex-col gap-3"
            >
              <button
                onClick={() => setMode('manual')}
                className="flex items-center gap-3
                  px-4 py-3 rounded-xl
                  cursor-pointer transition-all
                  hover:scale-[1.02]"
                style={{
                  background:
                    'var(--bg-elevated)',
                  border:
                    '1px solid var(--border)',
                  color: 'var(--text)',
                }}
              >
                <Server size={20} />
                <div className="text-left">
                  <div className="text-sm
                    font-medium"
                  >
                    Enter Manually
                  </div>
                  <div
                    className="text-xs"
                    style={{
                      color:
                        'var(--text-muted)',
                    }}
                  >
                    Specify controller URL
                  </div>
                </div>
              </button>

              <button
                onClick={() => void handleScan()}
                className="flex items-center gap-3
                  px-4 py-3 rounded-xl
                  cursor-pointer transition-all
                  hover:scale-[1.02]"
                style={{
                  background:
                    'var(--bg-elevated)',
                  border:
                    '1px solid var(--border)',
                  color: 'var(--text)',
                }}
              >
                <Wifi size={20} />
                <div className="text-left">
                  <div className="text-sm
                    font-medium"
                  >
                    Scan Network
                  </div>
                  <div
                    className="text-xs"
                    style={{
                      color:
                        'var(--text-muted)',
                    }}
                  >
                    Auto-discover controllers
                  </div>
                </div>
              </button>
            </motion.div>
          )}

          {/* ── Scan results ─────────────── */}
          {mode === 'scan' && (
            <motion.div
              key="scan"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
            >
              <button
                onClick={() => setMode('choose')}
                className="flex items-center gap-1
                  text-xs mb-4 cursor-pointer"
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--text-muted)',
                }}
              >
                <ArrowLeft size={14} /> Back
              </button>

              {scanning && (
                <div
                  className="flex flex-col
                    items-center gap-3 py-8"
                >
                  <Loader2
                    size={24}
                    className="animate-spin"
                    style={{
                      color: 'var(--primary)',
                    }}
                  />
                  <p
                    className="text-sm"
                    style={{
                      color:
                        'var(--text-muted)',
                    }}
                  >
                    Scanning network…
                  </p>
                </div>
              )}

              {!scanning && error && (
                <div
                  className="flex items-center
                    gap-2 text-xs mb-3 px-3
                    py-2 rounded-lg"
                  style={{
                    background:
                      'rgba(255,71,87,0.12)',
                    color: 'var(--error)',
                  }}
                >
                  <AlertCircle size={14} />
                  {error}
                </div>
              )}

              {!scanning
                && !error
                && devices.length === 0 && (
                <p
                  className="text-sm text-center
                    py-6"
                  style={{
                    color:
                      'var(--text-muted)',
                  }}
                >
                  No controllers found on the
                  network.
                </p>
              )}

              {!scanning
                && devices.length > 0 && (
                <div
                  className="flex flex-col gap-2"
                >
                  <p
                    className="text-xs mb-1"
                    style={{
                      color:
                        'var(--text-muted)',
                    }}
                  >
                    Found {devices.length}{' '}
                    controller
                    {devices.length > 1
                      ? 's'
                      : ''}
                    — choose a connection:
                  </p>
                  {devices.flatMap((d) => {
                    const ip = d.host;
                    const portSuffix =
                      d.port && d.port !== 443
                        ? `:${d.port}`
                        : '';
                    const ipLabel =
                      `${ip}${portSuffix}`;
                    const options: {
                      label: string;
                      url: string;
                      sub: string;
                    }[] = [
                      {
                        label: ipLabel,
                        url: `${ip}${portSuffix ? `:${d.port}` : ''}`,
                        sub: `IP address · ${d.device_type}`,
                      },
                    ];
                    if (d.hostname) {
                      options.push({
                        label: `${d.hostname}${portSuffix}`,
                        url: `${d.hostname}${portSuffix ? `:${d.port}` : ''}`,
                        sub: `Hostname · ${d.device_type}`,
                      });
                    }
                    return options.map((opt) => (
                      <button
                        key={opt.url}
                        onClick={() => {
                          setHost(opt.url);
                          setError('');
                          setMode('credentials');
                        }}
                        className="flex
                          items-center gap-3
                          px-4 py-3 rounded-xl
                          cursor-pointer
                          transition-all
                          hover:scale-[1.02]
                          text-left"
                        style={{
                          background:
                            'var(--bg-elevated)',
                          border:
                            '1px solid var(--border)',
                          color: 'var(--text)',
                          width: '100%',
                        }}
                      >
                        <Server size={16} />
                        <div>
                          <div
                            className="text-sm
                              font-medium"
                          >
                            {opt.label}
                          </div>
                          <div
                            className="text-xs"
                            style={{
                              color:
                                'var(--text-muted)',
                            }}
                          >
                            {opt.sub}
                          </div>
                        </div>
                      </button>
                    ));
                  })}
                </div>
              )}

              {/* Always show manual entry option */}
              {!scanning && (
                <button
                  onClick={() => setMode('manual')}
                  className="flex items-center gap-3
                    px-4 py-3 rounded-xl w-full
                    cursor-pointer transition-all
                    hover:scale-[1.02] text-left
                    mt-2"
                  style={{
                    background: 'transparent',
                    border:
                      '1px dashed var(--border)',
                    color: 'var(--text-muted)',
                  }}
                >
                  <Server size={16} />
                  <div>
                    <div
                      className="text-sm
                        font-medium"
                    >
                      Enter manually
                    </div>
                    <div className="text-xs">
                      Use a custom URL
                    </div>
                  </div>
                </button>
              )}
            </motion.div>
          )}

          {/* ── Manual host entry ────────── */}
          {mode === 'manual' && (
            <motion.div
              key="manual"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
            >
              <button
                onClick={() => setMode('choose')}
                className="flex items-center gap-1
                  text-xs mb-4 cursor-pointer"
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--text-muted)',
                }}
              >
                <ArrowLeft size={14} /> Back
              </button>
              <label
                className="text-xs font-medium
                  block mb-1.5"
                style={{
                  color: 'var(--text-muted)',
                }}
              >
                Controller URL
              </label>
              <input
                type="url"
                placeholder="https://192.168.1.1:8443"
                value={host}
                onChange={(e) =>
                  setHost(e.target.value)
                }
                style={inputStyle}
              />
              <button
                onClick={() => {
                  if (host)
                    setMode('credentials');
                }}
                disabled={!host}
                className="w-full mt-4 py-2.5
                  rounded-lg text-sm font-medium
                  cursor-pointer transition-all"
                style={{
                  background: host
                    ? 'var(--primary)'
                    : 'var(--bg-elevated)',
                  color: host
                    ? '#fff'
                    : 'var(--text-muted)',
                  border: 'none',
                }}
              >
                Continue
              </button>
            </motion.div>
          )}

          {/* ── Credentials form ─────────── */}
          {mode === 'credentials' && (
            <motion.div
              key="creds"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
            >
              <button
                onClick={() =>
                  setMode('choose')
                }
                className="flex items-center gap-1
                  text-xs mb-4 cursor-pointer"
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--text-muted)',
                }}
              >
                <ArrowLeft size={14} /> Back
              </button>

              <p
                className="text-xs mb-4
                  truncate"
                style={{
                  color: 'var(--text-muted)',
                }}
              >
                Connecting to{' '}
                <strong
                  style={{
                    color: 'var(--primary)',
                  }}
                >
                  {host}
                </strong>
              </p>

              <form
                onSubmit={(e) =>
                  void handleSubmit(e)
                }
                className={
                  error ? 'animate-shake' : ''
                }
              >
                <label
                  className="text-xs font-medium
                    block mb-1.5"
                  style={{
                    color:
                      'var(--text-muted)',
                  }}
                >
                  Username
                </label>
                <input
                  value={username}
                  onChange={(e) =>
                    setUsername(e.target.value)
                  }
                  style={inputStyle}
                  className="mb-3"
                  autoFocus
                />

                <label
                  className="text-xs font-medium
                    block mb-1.5"
                  style={{
                    color:
                      'var(--text-muted)',
                  }}
                >
                  Password
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) =>
                    setPassword(e.target.value)
                  }
                  style={inputStyle}
                  className="mb-3"
                />

                {/* Remember toggle */}
                <label
                  className="flex items-center
                    gap-2 text-xs mb-4
                    cursor-pointer select-none"
                  style={{
                    color:
                      'var(--text-muted)',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={remember}
                    onChange={(e) =>
                      setRemember(
                        e.target.checked,
                      )
                    }
                    className="accent-blue-500"
                  />
                  Remember for 90 days
                </label>

                {/* Error */}
                {error && (
                  <div
                    className="flex items-center
                      gap-2 text-xs mb-3 px-3
                      py-2 rounded-lg"
                    style={{
                      background:
                        'rgba(255,71,87,0.12)',
                      color: 'var(--error)',
                    }}
                  >
                    <AlertCircle size={14} />
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={
                    isLoading
                    || !username
                    || !password
                  }
                  className="w-full py-2.5
                    rounded-lg text-sm
                    font-medium cursor-pointer
                    transition-all flex
                    items-center justify-center
                    gap-2"
                  style={{
                    background:
                      'var(--primary)',
                    color: '#fff',
                    border: 'none',
                    opacity:
                      isLoading
                      || !username
                      || !password
                        ? 0.6
                        : 1,
                  }}
                >
                  {isLoading && (
                    <Loader2
                      size={16}
                      className="animate-spin"
                    />
                  )}
                  {isLoading
                    ? 'Connecting…'
                    : 'Sign In'}
                </button>
              </form>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
      <div
        className="fixed bottom-2 right-3 text-[10px] select-none opacity-30"
        style={{ color: 'var(--text-muted)' }}
      >
        v{__APP_VERSION__}
      </div>
    </div>
  );
}
