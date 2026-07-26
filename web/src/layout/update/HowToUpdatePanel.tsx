import { useState } from 'react';
import { Check, Copy, Terminal } from 'lucide-react';
import { Button } from '../../components/ui/Button';
import type { InstallMethod, UpdateVariant } from '../../api/update';

/**
 * "How to update" instructions for every install method that isn't a real,
 * self-detected pip venv (`detect.py`'s `self_upgrade_supported`). Never a fake
 * button standing in for an action the daemon can't actually perform — each
 * method gets the exact, correct instruction (docs/ARCHITECTURE.md §23):
 *
 *   - container (macmini variant): `./deploy/update-macmini.sh`
 *   - container (compose variant, or unspecified): `git pull && docker compose
 *     up -d --build`
 *   - addon: Settings → Add-ons → UnifiOptimizer → Update (no host command —
 *     Supervisor owns the image)
 *   - source: `git pull && bash install.sh`
 *   - pip without self-upgrade support (a system-wide or VCS install): the
 *     plain `pip install --upgrade` a human would run by hand
 */

interface Content {
  title: string;
  body: string;
  command?: string;
}

function contentFor(method: InstallMethod, variant: UpdateVariant): Content {
  switch (method) {
    case 'container':
      return variant === 'macmini'
        ? {
            title: 'Update the Mac mini deploy',
            body: 'Run this on the Mac mini. It rebuilds and restarts the container in place.',
            command: './deploy/update-macmini.sh',
          }
        : {
            title: 'Update the container',
            body: 'Run this on the host running the container. It rebuilds the image and restarts it.',
            command: 'git pull && docker compose up -d --build',
          };
    case 'addon':
      return {
        title: 'Update the add-on',
        body: 'Home Assistant manages this install. Go to Settings → Add-ons → UnifiOptimizer → Update.',
      };
    case 'source':
      return {
        title: 'Update the source checkout',
        body: 'Run this in the checkout. It pulls the latest code and re-runs the installer.',
        command: 'git pull && bash install.sh',
      };
    case 'pip':
    default:
      return {
        title: 'Update the pip install',
        body: 'This install could not be confirmed as a self-updatable virtual environment, so update it by hand:',
        command: 'pip install --upgrade unifioptimizer',
      };
  }
}

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function HowToUpdatePanel({
  method,
  variant,
  onClose,
}: {
  method: InstallMethod;
  variant: UpdateVariant;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const { title, body, command } = contentFor(method, variant);

  const onCopy = async () => {
    if (!command) return;
    if (await copy(command)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-6 py-10"
      style={{ background: 'color-mix(in srgb, var(--canvas) 68%, transparent)' }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={onKeyDown}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="howto-sheet-title"
        className="w-full max-w-[440px] rounded-card p-5 flex flex-col"
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
            <Terminal size={16} />
          </span>
          <div className="flex flex-col gap-1">
            <h1 id="howto-sheet-title" className="t-section" style={{ color: 'var(--fg)' }}>
              {title}
            </h1>
          </div>
        </div>

        <p className="t-body mb-3" style={{ color: 'var(--fg)' }}>
          {body}
        </p>

        {command && (
          <div
            className="flex items-center justify-between gap-3 px-3 h-10 rounded-control mb-4"
            style={{ background: 'var(--canvas)', border: '1px solid var(--hairline)' }}
          >
            <code className="font-mono truncate" style={{ fontSize: 13, color: 'var(--fg)' }}>
              {command}
            </code>
            <button
              type="button"
              onClick={onCopy}
              aria-label="Copy command"
              className="inline-flex items-center gap-1.5 t-caption shrink-0 cursor-pointer"
              style={{ color: copied ? 'var(--sev-healthy)' : 'var(--fg-subtle)' }}
            >
              {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
        )}

        <div className="flex items-center justify-end">
          <Button variant="secondary" size="md" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}
