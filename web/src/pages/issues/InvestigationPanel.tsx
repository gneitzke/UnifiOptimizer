import { useCallback, useEffect, useState } from 'react';
import { Check, Copy, Download, FlaskConical } from 'lucide-react';
import { Button } from '../../components/ui/Button';
import { RelativeTime } from '../../components/ui/RelativeTime';
import { Skeleton } from '../../components/ui/Skeleton';
import {
  importInvestigation,
  listInvestigationProviders,
  listInvestigations,
  startInvestigation,
  type InvestigationRow,
  type ProviderInfo,
} from './investigation';
import { SafeMarkdown } from './SafeMarkdown';

/**
 * Issue-detail investigation section (§10). Pick an available provider and
 * generate a dossier: `manual` writes it here to copy/download and run through
 * any model, then paste the response back; `copilot` / `anthropic` return the
 * answer directly. The thread renders every investigation's response as
 * sanitized Markdown (no raw HTML injection). Nothing here mutates the network.
 */

const PROVIDER_LABEL: Record<string, string> = {
  manual: 'Manual',
  copilot: 'Copilot CLI',
  anthropic: 'Claude API',
};

function StatusChip({ status }: { status: string }) {
  const answered = status === 'answered';
  return (
    <span
      className="inline-flex items-center gap-1 h-[20px] px-1.5 rounded-full text-[12px] font-medium"
      style={{ background: 'var(--sev-neutral-fill)', color: 'var(--fg-muted)' }}
    >
      {answered && <Check size={12} style={{ color: 'var(--fg-muted)' }} />}
      {answered ? 'answered' : 'pending'}
    </span>
  );
}

function DossierDisclosure({ inv, onCopy, onDownload }: {
  inv: InvestigationRow;
  onCopy: () => void;
  onDownload: () => void;
}) {
  return (
    <details>
      <summary
        className="t-caption cursor-pointer select-none"
        style={{ color: 'var(--fg-muted)' }}
      >
        Dossier ({inv.dossier_md.length.toLocaleString()} chars)
      </summary>
      <div className="flex items-center gap-2 mt-2">
        <Button variant="secondary" size="sm" onClick={onCopy}>
          <Copy size={13} /> Copy
        </Button>
        <Button variant="secondary" size="sm" onClick={onDownload}>
          <Download size={13} /> Download .md
        </Button>
      </div>
      <div
        className="mt-2 p-3 rounded-control"
        style={{
          background: 'var(--canvas)',
          border: '1px solid var(--hairline)',
          // The dossier is a long reference blob (often 4-5k chars); contain it in
          // its own scroll area so expanding the disclosure never balloons the page
          // to several screens (DESIGN_FOUNDATION: contain anything over a screenful).
          maxHeight: 420,
          overflowY: 'auto',
        }}
      >
        <SafeMarkdown markdown={inv.dossier_md} />
      </div>
    </details>
  );
}

export function InvestigationPanel({
  issueId,
  onInvestigated,
}: {
  issueId: number;
  onInvestigated?: () => void;
}) {
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [selected, setSelected] = useState('manual');
  const [investigations, setInvestigations] = useState<InvestigationRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importText, setImportText] = useState('');
  const [copiedId, setCopiedId] = useState<number | null>(null);

  const load = useCallback(async () => {
    const [provs, invs] = await Promise.all([
      listInvestigationProviders(),
      listInvestigations(issueId),
    ]);
    setProviders(provs);
    setInvestigations(invs);
  }, [issueId]);

  useEffect(() => {
    load().catch((e) => setError((e as Error).message || 'Could not load investigations'));
  }, [load]);

  async function runAction(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      onInvestigated?.();
    } catch (e) {
      setError((e as Error).message || 'Action failed');
    } finally {
      setBusy(false);
    }
  }

  function copyDossier(inv: InvestigationRow) {
    navigator.clipboard?.writeText(inv.dossier_md).then(
      () => {
        setCopiedId(inv.id);
        window.setTimeout(() => setCopiedId((c) => (c === inv.id ? null : c)), 1500);
      },
      () => setError('Clipboard unavailable'),
    );
  }

  function downloadDossier(inv: InvestigationRow) {
    const blob = new Blob([inv.dossier_md], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `issue-${issueId}-investigation-${inv.id}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (providers === null || investigations === null) {
    return <Skeleton className="h-24 w-full" />;
  }

  const thread = [...investigations].reverse(); // newest first
  const newestPendingId = thread.find((i) => i.status !== 'answered')?.id ?? null;

  return (
    <div className="flex flex-col gap-4">
      <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
        Compile a dossier from this issue's trail, evidence, ruled-out confounders, related
        issues, and the detector's playbook, then have a model reason over it. Nothing is
        auto-applied.
      </p>

      {/* Provider picker */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
          Provider
        </span>
        {providers.map((p) => {
          const active = p.name === selected;
          return (
            <button
              key={p.name}
              type="button"
              disabled={!p.available || busy}
              title={p.detail}
              onClick={() => setSelected(p.name)}
              className="inline-flex items-center h-8 px-3 rounded-control t-caption font-medium transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
              style={{
                border: `1px solid ${active ? 'var(--accent)' : 'var(--strong)'}`,
                color: active ? 'var(--accent)' : 'var(--fg-muted)',
                background: active ? 'var(--accent-muted)' : 'transparent',
              }}
            >
              {PROVIDER_LABEL[p.name] ?? p.name}
            </button>
          );
        })}
        <Button
          variant="primary"
          size="sm"
          disabled={busy}
          onClick={() => runAction(() => startInvestigation(issueId, selected))}
        >
          <FlaskConical size={14} />
          {busy ? 'Working…' : 'Generate dossier'}
        </Button>
      </div>

      {error && (
        <span className="t-micro" style={{ color: 'var(--sev-p1)' }}>
          {error}
        </span>
      )}

      {/* Thread */}
      {thread.length === 0 ? (
        <p className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
          No investigations yet.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {thread.map((inv) => (
            <div
              key={inv.id}
              className="flex flex-col gap-3 p-3 rounded-control"
              style={{ border: '1px solid var(--hairline)', background: 'var(--surface)' }}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className="t-caption font-medium" style={{ color: 'var(--fg)' }}>
                  {PROVIDER_LABEL[inv.provider] ?? inv.provider}
                </span>
                <StatusChip status={inv.status} />
                <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
                  <RelativeTime ts={inv.ts} mode="relative" />
                </span>
                {copiedId === inv.id && (
                  // Accent, not green: green is reserved for health signals
                  // (DESIGN_FOUNDATION) so "copied" doesn't read as a health state.
                  <span className="t-micro" style={{ color: 'var(--accent)' }}>
                    copied
                  </span>
                )}
              </div>

              {inv.dossier_md && (
                <DossierDisclosure
                  inv={inv}
                  onCopy={() => copyDossier(inv)}
                  onDownload={() => downloadDossier(inv)}
                />
              )}

              {inv.response_md ? (
                <div className="flex flex-col gap-1.5">
                  <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
                    Response
                  </span>
                  <SafeMarkdown markdown={inv.response_md} />
                </div>
              ) : (
                inv.id === newestPendingId && (
                  <div className="flex flex-col gap-2">
                    <span className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
                      Paste the model's response to attach it:
                    </span>
                    <textarea
                      value={importText}
                      onChange={(e) => setImportText(e.target.value)}
                      placeholder="## Answers&#10;### Root cause&#10;…"
                      rows={5}
                      className="w-full rounded-control p-2.5 t-caption resize-y"
                      style={{
                        background: 'var(--canvas)',
                        border: '1px solid var(--strong)',
                        color: 'var(--fg)',
                        fontFamily: 'var(--font-mono)',
                      }}
                    />
                    <div>
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled={busy || importText.trim() === ''}
                        onClick={() =>
                          runAction(async () => {
                            await importInvestigation(issueId, importText);
                            setImportText('');
                          })
                        }
                      >
                        Attach response
                      </Button>
                    </div>
                  </div>
                )
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
