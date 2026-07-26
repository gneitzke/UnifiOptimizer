import { useState } from 'react';
import { Check, Copy, Eye, EyeOff, RefreshCw } from 'lucide-react';
import { Button, Card } from '../../components/ui';
import {
  clearToken,
  getToken,
  regenerateSystemToken,
  revealSystemToken,
  setToken,
} from '../../api';

/**
 * Settings → Access token (docs/ARCHITECTURE.md §18.1 Settings addendum).
 *
 * How a user finds or rotates the token a just-in-time fix prompt asks for:
 *
 *   - Reveal  — `GET /api/system/token`. Masked until asked. From another device
 *     it needs the token (the JIT prompt supplies it); on the box (loopback) it
 *     just shows. This is the recovery path for a forgotten token.
 *   - Regenerate — `POST /api/system/token/regenerate`. A two-step confirm mints a
 *     new token, stores it in this browser so fixes keep working, and shows it once.
 *     The old token stops working everywhere.
 *
 * Both themes from the shared tokens; no token value is ever logged.
 */

type RevealState =
  | { kind: 'masked' }
  | { kind: 'loading' }
  | { kind: 'shown'; token: string | null; configured: boolean }
  | { kind: 'error'; message: string };

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function AccessTokenSection() {
  const [reveal, setReveal] = useState<RevealState>({ kind: 'masked' });
  const [confirmRegen, setConfirmRegen] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [copied, setCopied] = useState(false);
  const [justRotated, setJustRotated] = useState(false);

  const hasBrowserToken = getToken() !== null;

  const doReveal = async () => {
    setReveal({ kind: 'loading' });
    try {
      const info = await revealSystemToken();
      setReveal({ kind: 'shown', token: info.token, configured: info.configured });
    } catch (e) {
      // A dismissed prompt or a rejected token: fall back to masked, honestly.
      const status = (e as { status?: number }).status;
      setReveal(
        status === 401 || status === undefined
          ? { kind: 'masked' }
          : { kind: 'error', message: `Couldn't reveal the token (error ${status}).` },
      );
    }
  };

  const doRegenerate = async () => {
    setRegenerating(true);
    try {
      const { token } = await regenerateSystemToken();
      // Keep this browser working: store the new token so the next fix doesn't
      // re-prompt. Then show it once so the operator can save it elsewhere.
      setToken(token);
      setReveal({ kind: 'shown', token, configured: true });
      setJustRotated(true);
      setConfirmRegen(false);
    } catch {
      // Dismissed / rejected: leave state as-is; the section stays usable.
      setConfirmRegen(false);
    } finally {
      setRegenerating(false);
    }
  };

  const onCopy = async (token: string) => {
    if (await copy(token)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  };

  const shownToken = reveal.kind === 'shown' ? reveal.token : null;

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1 min-w-0">
          <span className="t-body" style={{ color: 'var(--fg)' }}>
            Access token
          </span>
          <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
            The token a fix prompts you for before applying changes. Viewing never
            needs it.
          </span>
        </div>
        {reveal.kind === 'shown' ? (
          <Button variant="ghost" size="sm" onClick={() => setReveal({ kind: 'masked' })}>
            <EyeOff size={15} aria-hidden />
            Hide
          </Button>
        ) : (
          <Button
            variant="secondary"
            size="sm"
            disabled={reveal.kind === 'loading'}
            onClick={doReveal}
          >
            <Eye size={15} aria-hidden />
            {reveal.kind === 'loading' ? 'Revealing…' : 'Reveal'}
          </Button>
        )}
      </div>

      {/* The value row: masked dots, or the revealed token with a copy affordance. */}
      <div
        className="flex items-center justify-between gap-3 px-3 h-10 rounded-control"
        style={{ background: 'var(--canvas)', border: '1px solid var(--hairline)' }}
      >
        {reveal.kind === 'shown' && shownToken ? (
          <>
            <span
              className="font-mono truncate"
              style={{ fontSize: 13, color: 'var(--fg)' }}
              title={shownToken}
            >
              {shownToken}
            </span>
            <button
              type="button"
              onClick={() => onCopy(shownToken)}
              aria-label="Copy access token"
              className="inline-flex items-center gap-1.5 t-caption shrink-0 cursor-pointer"
              style={{ color: copied ? 'var(--sev-healthy)' : 'var(--fg-subtle)' }}
            >
              {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          </>
        ) : reveal.kind === 'shown' && !reveal.configured ? (
          <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
            No token set — this daemon is open on the LAN.
          </span>
        ) : (
          <span className="font-mono select-none" style={{ fontSize: 13, color: 'var(--fg-subtle)' }}>
            {'•'.repeat(28)}
          </span>
        )}
      </div>

      {justRotated && reveal.kind === 'shown' && (
        <p className="t-caption" style={{ color: 'var(--fg-muted)' }}>
          New token generated and saved to this browser. Save it somewhere safe — the
          old token has stopped working, so any other device must use this one.
        </p>
      )}

      {reveal.kind === 'error' && (
        <p role="alert" className="t-caption" style={{ color: 'var(--sev-p1)' }}>
          {reveal.message}
        </p>
      )}

      {/* Rotate + forget. Regenerate is a two-step confirm (it invalidates the old
          token everywhere); forget only clears this browser's copy. */}
      <div
        className="flex items-center justify-between gap-3 pt-3"
        style={{ borderTop: '1px solid var(--hairline)' }}
      >
        {confirmRegen ? (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="t-caption" style={{ color: 'var(--fg-muted)' }}>
              Replace the token? The old one stops working everywhere.
            </span>
            <Button variant="primary" size="sm" disabled={regenerating} onClick={doRegenerate}>
              {regenerating ? 'Generating…' : 'Replace token'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={regenerating}
              onClick={() => setConfirmRegen(false)}
            >
              Cancel
            </Button>
          </div>
        ) : (
          <Button variant="secondary" size="sm" onClick={() => setConfirmRegen(true)}>
            <RefreshCw size={15} aria-hidden />
            Regenerate
          </Button>
        )}

        {hasBrowserToken && (
          <Button variant="ghost" size="sm" onClick={clearToken}>
            Forget on this browser
          </Button>
        )}
      </div>
    </Card>
  );
}
