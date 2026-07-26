import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Search, Sun, type LucideIcon } from 'lucide-react';
import { NAV_ITEMS } from './nav';
import { useCommandPalette } from './keyboard/commandPaletteContext';
import { useListNavigation } from './keyboard/useListNavigation';
import { useHotkeys } from './keyboard/useHotkeys';
import { useTheme } from '../theme';
import { Card } from '../components/ui/Card';

/**
 * Cmd+K command palette (docs §Interaction: "a palette that teaches shortcuts
 * inline"). Skeleton scope: it jumps to destinations and toggles theme, teaches
 * the shortcut model in its footer, and is fully keyboard-driven (↑↓/j-k, ↵,
 * Esc). Page agents extend it with entity search next.
 *
 * The body is a separate component mounted only while open, so each open starts
 * from a fresh, empty query with no reset effect.
 */

interface Command {
  id: string;
  label: string;
  hint?: string;
  icon: LucideIcon;
  run: () => void;
}

export function CommandPalette() {
  const { open, setOpen } = useCommandPalette();
  if (!open) return null;
  return <PaletteBody onClose={() => setOpen(false)} />;
}

function PaletteBody({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);

  const commands = useMemo<Command[]>(() => {
    const nav = NAV_ITEMS.map((n) => ({
      id: `nav:${n.to}`,
      label: `Go to ${n.label}`,
      hint: n.to,
      icon: n.icon,
      run: () => navigate(n.to),
    }));
    const actions: Command[] = [
      {
        id: 'theme',
        label: theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme',
        hint: 'theme',
        icon: theme === 'dark' ? Sun : Moon,
        run: toggle,
      },
    ];
    return [...nav, ...actions];
  }, [navigate, theme, toggle]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => c.label.toLowerCase().includes(q));
  }, [commands, query]);

  const nav = useListNavigation(
    filtered.length,
    (i) => {
      const cmd = filtered[i];
      if (cmd) {
        cmd.run();
        onClose();
      }
    },
    { wrap: true, initialIndex: 0 },
  );

  // Focus the input on open (a ref read in an effect, no setState).
  useEffect(() => {
    const id = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, []);

  useHotkeys([{ combo: 'escape', handler: onClose, allowInInput: true }]);

  return (
    <div
      className="fixed inset-0 z-50 flex justify-center px-4 pt-[14vh]"
      style={{ background: 'var(--overlay)' }}
      onClick={onClose}
    >
      <Card
        elevated
        pad="none"
        className="w-full max-w-xl h-fit overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={nav.containerProps.onKeyDown}
      >
        <div
          className="flex items-center gap-2 px-3 h-12"
          style={{ borderBottom: '1px solid var(--hairline)' }}
        >
          <Search size={16} style={{ color: 'var(--fg-subtle)' }} />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              nav.setActiveIndex(0); // reset highlight to the top on filter change
            }}
            placeholder="Search commands…"
            className="flex-1 bg-transparent outline-none t-body"
            style={{ color: 'var(--fg)' }}
            aria-label="Command palette search"
          />
          <Kbd>⌘K</Kbd>
        </div>

        <ul className="max-h-80 overflow-y-auto py-1" role="listbox" aria-label="Commands">
          {filtered.length === 0 && (
            <li
              className="px-3 py-6 text-center t-secondary"
              style={{ color: 'var(--fg-subtle)' }}
            >
              No commands match “{query}”.
            </li>
          )}
          {filtered.map((cmd, i) => {
            const Icon = cmd.icon;
            const rp = nav.getRowProps(i);
            const active = i === nav.activeIndex;
            return (
              <li
                key={cmd.id}
                ref={rp.ref as (el: HTMLLIElement | null) => void}
                role="option"
                aria-selected={active}
                onMouseEnter={rp.onMouseEnter}
                onClick={() => {
                  cmd.run();
                  onClose();
                }}
                className="flex items-center gap-3 mx-1 px-2 h-10 rounded-control cursor-pointer"
                style={{ background: active ? 'var(--canvas)' : undefined }}
              >
                <Icon size={16} style={{ color: 'var(--fg-subtle)' }} />
                <span className="flex-1 t-body" style={{ color: 'var(--fg)' }}>
                  {cmd.label}
                </span>
                {cmd.hint && (
                  <span className="t-caption font-mono" style={{ color: 'var(--fg-subtle)' }}>
                    {cmd.hint}
                  </span>
                )}
              </li>
            );
          })}
        </ul>

        {/* Teach the shortcut model inline. */}
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 h-10 t-caption"
          style={{ borderTop: '1px solid var(--hairline)', color: 'var(--fg-subtle)' }}
        >
          <ShortcutHint keys={['↑', '↓']} label="navigate" />
          <ShortcutHint keys={['↵']} label="open" />
          <ShortcutHint keys={['/']} label="filter a list" />
          <ShortcutHint keys={['j', 'k']} label="rows" />
          <ShortcutHint keys={['esc']} label="close" />
        </div>
      </Card>
    </div>
  );
}

function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd
      className="px-1.5 h-5 inline-flex items-center rounded text-[11px] font-mono"
      style={{
        background: 'var(--canvas)',
        border: '1px solid var(--hairline)',
        color: 'var(--fg-subtle)',
      }}
    >
      {children}
    </kbd>
  );
}

function ShortcutHint({ keys, label }: { keys: string[]; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {keys.map((k) => (
        <Kbd key={k}>{k}</Kbd>
      ))}
      <span className="ml-0.5">{label}</span>
    </span>
  );
}
