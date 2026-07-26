import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Eye, EyeOff, KeyRound } from 'lucide-react';
import { Button } from '../components/ui/Button';
import {
  cancelTokenPrompt,
  isTokenPromptOpen,
  submitTokenPrompt,
  subscribeAuth,
  tokenPromptMessage,
} from './token';

/**
 * Just-in-time access-token prompt (docs/ARCHITECTURE.md §18.1).
 *
 * Viewing is never gated. This small modal appears only when a MUTATING action
 * (apply a fix, ack/snooze, regenerate the token) is attempted without a valid
 * token — the dashboard stays visible underneath. On submit the token is stored
 * and the original action resumes; on dismiss the action surfaces its 401 and the
 * user stays exactly where they were.
 *
 * Mounted once at the app root, outside the router, so any mutating call anywhere
 * can raise it. Both themes from the shared tokens; keyboard-first (autofocus,
 * Enter submits, Escape cancels).
 */

export function TokenPrompt() {
  const [open, setOpen] = useState(isTokenPromptOpen());
  const [message, setMessage] = useState(tokenPromptMessage());
  const [value, setValue] = useState('');
  const [show, setShow] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const openRef = useRef(open);

  // Sync from the token module. Clearing the field on the closed→open edge happens
  // here (a subscription callback, not an effect body) so each open starts fresh.
  useEffect(
    () =>
      subscribeAuth(() => {
        const nowOpen = isTokenPromptOpen();
        if (nowOpen && !openRef.current) {
          setValue('');
          setShow(false);
        }
        openRef.current = nowOpen;
        setOpen(nowOpen);
        setMessage(tokenPromptMessage());
      }),
    [],
  );

  // Move focus into the field when the modal opens (DOM sync only, no state).
  useEffect(() => {
    if (!open) return undefined;
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open]);

  if (!open) return null;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!value.trim()) return;
    submitTokenPrompt(value);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      cancelTokenPrompt();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-6 py-10"
      style={{ background: 'color-mix(in srgb, var(--canvas) 68%, transparent)' }}
      onMouseDown={(e) => {
        // Backdrop click dismisses (but not a drag that started inside the card).
        if (e.target === e.currentTarget) cancelTokenPrompt();
      }}
      onKeyDown={onKeyDown}
    >
      <form
        onSubmit={submit}
        role="dialog"
        aria-modal="true"
        aria-labelledby="token-prompt-title"
        className="w-full max-w-[400px] rounded-card p-5 flex flex-col"
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--hairline)',
          boxShadow: 'var(--shadow-elevated)',
        }}
      >
        <div className="flex items-start gap-3 mb-3">
          <span
            aria-hidden
            className="inline-flex items-center justify-center w-8 h-8 rounded-control shrink-0"
            style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
          >
            <KeyRound size={16} />
          </span>
          <div className="flex flex-col gap-1">
            <h1 id="token-prompt-title" className="t-section" style={{ color: 'var(--fg)' }}>
              Enter your access token
            </h1>
            <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
              Applying changes needs the access token shown when you set
              UnifiOptimizer up. Viewing stays open.
            </p>
          </div>
        </div>

        <label htmlFor="jit-token" className="t-label mb-1.5" style={{ color: 'var(--fg)' }}>
          Access token
        </label>
        <div className="relative">
          <input
            id="jit-token"
            ref={inputRef}
            type={show ? 'text' : 'password'}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-invalid={Boolean(message)}
            placeholder="Access token"
            className="w-full h-9 pl-3 pr-10 rounded-control font-mono outline-none"
            style={{
              background: 'var(--canvas)',
              border: `1px solid ${message ? 'var(--sev-p1)' : 'var(--strong)'}`,
              color: 'var(--fg)',
              fontSize: 13,
            }}
          />
          <button
            type="button"
            onClick={() => setShow((s) => !s)}
            aria-label={show ? 'Hide token' : 'Show token'}
            aria-pressed={show}
            className="absolute right-1 top-1 inline-flex items-center justify-center w-7 h-7 rounded-control cursor-pointer transition-colors hover:bg-canvas"
            style={{ color: 'var(--fg-subtle)' }}
          >
            {show ? <EyeOff size={15} /> : <Eye size={15} />}
          </button>
        </div>

        {message && (
          <p role="alert" className="t-caption mt-2" style={{ color: 'var(--sev-p1)' }}>
            {message}
          </p>
        )}

        <div className="flex items-center justify-end gap-2 mt-4">
          <Button type="button" variant="ghost" size="md" onClick={() => cancelTokenPrompt()}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" size="md" disabled={value.trim().length === 0}>
            Save &amp; continue
          </Button>
        </div>

        <p className="t-caption mt-3" style={{ color: 'var(--fg-subtle)' }}>
          Find it under Settings → Access token, or in{' '}
          <span className="font-mono" style={{ fontSize: 11.5 }}>
            data/secrets.env
          </span>
          . Stored in this browser only.
        </p>
      </form>
    </div>
  );
}
