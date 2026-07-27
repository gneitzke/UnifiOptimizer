import { useState } from 'react';
import { Check, Copy, Eye, EyeOff, RefreshCw, Terminal } from 'lucide-react';
import { Button, Card } from '../../components/ui';
import { regenerateMcpToken, revealMcpToken } from '../../api';

/**
 * Settings → Remote MCP token (docs/ARCHITECTURE.md §18.4 Settings addendum).
 *
 * How a user finds or rotates `NETADMIN_MCP_TOKEN` — the credential a Claude
 * client on another machine presents to `/mcp` to read this daemon's history
 * over the network. Same shape as `AccessTokenSection`, and gated by that same
 * access token (never by the MCP token itself):
 *
 *   - Reveal — `GET /api/system/mcp-token`. Masked until asked. From another
 *     device it needs the access token; on the box (loopback) it just shows.
 *   - Regenerate — `POST /api/system/mcp-token/regenerate`. A two-step confirm
 *     mints a new token and shows it once. The old one stops working the
 *     moment `/mcp` sees the next request, no daemon restart — unless remote
 *     MCP was off before this call, in which case its session was never built
 *     and still needs one restart to start serving.
 *
 * Unlike the access token, this one is never stored in this browser: it isn't
 * used to authenticate the browser's own requests, only pasted into a Claude
 * client's config elsewhere, so the Claude Code snippet below is the point.
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

// The origin this page is itself served from, in production the daemon's own
// origin. VITE_API_URL overrides it for a dev split (SPA on 5173, API on 8765).
const DAEMON_ORIGIN =
  (import.meta.env.VITE_API_URL as string | undefined) || window.location.origin;

function claudeCodeCommand(token: string): string {
  return (
    `claude mcp add --transport http unifioptimizer ${DAEMON_ORIGIN}/mcp ` +
    `--header "Authorization: Bearer ${token}"`
  );
}

export function McpTokenSection() {
  const [reveal, setReveal] = useState<RevealState>({ kind: 'masked' });
  const [confirmRegen, setConfirmRegen] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [copiedToken, setCopiedToken] = useState(false);
  const [copiedCommand, setCopiedCommand] = useState(false);
  const [justRotated, setJustRotated] = useState(false);

  const doReveal = async () => {
    setReveal({ kind: 'loading' });
    try {
      const info = await revealMcpToken();
      setReveal({ kind: 'shown', token: info.token, configured: info.configured });
    } catch (e) {
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
      const { token } = await regenerateMcpToken();
      setReveal({ kind: 'shown', token, configured: true });
      setJustRotated(true);
      setConfirmRegen(false);
    } catch {
      setConfirmRegen(false);
    } finally {
      setRegenerating(false);
    }
  };

  const onCopyToken = async (token: string) => {
    if (await copy(token)) {
      setCopiedToken(true);
      window.setTimeout(() => setCopiedToken(false), 1500);
    }
  };

  const onCopyCommand = async (token: string) => {
    if (await copy(claudeCodeCommand(token))) {
      setCopiedCommand(true);
      window.setTimeout(() => setCopiedCommand(false), 1500);
    }
  };

  const shownToken = reveal.kind === 'shown' ? reveal.token : null;

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1 min-w-0">
          <span className="t-body" style={{ color: 'var(--fg)' }}>
            Remote MCP token
          </span>
          <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
            What a Claude client on another machine presents to read your history
            over the network. Read-only; it can never change your controller.
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
              onClick={() => onCopyToken(shownToken)}
              aria-label="Copy MCP token"
              className="inline-flex items-center gap-1.5 t-caption shrink-0 cursor-pointer"
              style={{ color: copiedToken ? 'var(--sev-healthy)' : 'var(--fg-subtle)' }}
            >
              {copiedToken ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
              {copiedToken ? 'Copied' : 'Copy'}
            </button>
          </>
        ) : reveal.kind === 'shown' && !reveal.configured ? (
          <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
            No token set. Remote MCP is off, and /mcp answers 404.
          </span>
        ) : (
          <span
            className="font-mono select-none"
            style={{ fontSize: 13, color: 'var(--fg-subtle)' }}
          >
            {'•'.repeat(28)}
          </span>
        )}
      </div>

      {/* The Claude Code snippet, only once the real token is on screen — never
          with a placeholder in it, so there is nothing here to paste and have
          silently fail. */}
      {reveal.kind === 'shown' && shownToken && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between gap-3">
            <span
              className="t-caption inline-flex items-center gap-1.5"
              style={{ color: 'var(--fg-muted)' }}
            >
              <Terminal size={13} aria-hidden />
              Claude Code
            </span>
            <button
              type="button"
              onClick={() => onCopyCommand(shownToken)}
              aria-label="Copy Claude Code command"
              className="inline-flex items-center gap-1.5 t-caption shrink-0 cursor-pointer"
              style={{ color: copiedCommand ? 'var(--sev-healthy)' : 'var(--fg-subtle)' }}
            >
              {copiedCommand ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
              {copiedCommand ? 'Copied' : 'Copy'}
            </button>
          </div>
          <code
            className="block w-full rounded-control p-2.5 font-mono break-all"
            style={{
              background: 'var(--canvas)',
              border: '1px solid var(--hairline)',
              color: 'var(--fg)',
              fontSize: 12.5,
              lineHeight: '19px',
            }}
          >
            {claudeCodeCommand(shownToken)}
          </code>
        </div>
      )}

      {justRotated && reveal.kind === 'shown' && (
        <p className="t-caption" style={{ color: 'var(--fg-muted)' }}>
          New token generated. The old one stops working as soon as /mcp sees the
          next request, no restart needed. If remote MCP was off before this, restart
          the daemon once so /mcp starts serving.
        </p>
      )}

      {reveal.kind === 'error' && (
        <p role="alert" className="t-caption" style={{ color: 'var(--sev-p1)' }}>
          {reveal.message}
        </p>
      )}

      {/* Rotate. Regenerate is a two-step confirm (it invalidates the old token
          on its very next /mcp request). */}
      <div
        className="flex items-center justify-between gap-3 pt-3"
        style={{ borderTop: '1px solid var(--hairline)' }}
      >
        {confirmRegen ? (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="t-caption" style={{ color: 'var(--fg-muted)' }}>
              Replace the token? The old one stops working right away.
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
      </div>
    </Card>
  );
}
