import { type RefObject, useEffect, useRef } from 'react';

/**
 * Keyboard-first primitives (docs §Interaction): Cmd+K palette, `/` filter,
 * j/k row traversal, Enter to open, Esc to close. This is the low-level combo
 * matcher the shell, palette, and lists all share.
 */

export interface Hotkey {
  /** e.g. "mod+k", "escape", "/", "j", "shift+?". "mod" = ⌘ on mac, Ctrl else. */
  combo: string;
  handler: (e: KeyboardEvent) => void;
  /** Fire even when focus is in a text field (default false). */
  allowInInput?: boolean;
  /** preventDefault when matched (default true). */
  preventDefault?: boolean;
}

const IS_MAC =
  typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform);

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    target.isContentEditable
  );
}

function matches(e: KeyboardEvent, combo: string): boolean {
  const parts = combo.toLowerCase().split('+');
  const key = parts[parts.length - 1];
  const needMod = parts.includes('mod');
  const needShift = parts.includes('shift');
  const needAlt = parts.includes('alt');

  const modOk = needMod ? (IS_MAC ? e.metaKey : e.ctrlKey) : !e.metaKey && !e.ctrlKey;
  const shiftOk = needShift ? e.shiftKey : true;
  const altOk = needAlt ? e.altKey : !e.altKey;
  if (!modOk || !shiftOk || !altOk) return false;

  const pressed = e.key.toLowerCase();
  if (key === pressed) return true;
  // Named aliases.
  if (key === 'escape' && pressed === 'esc') return true;
  if (key === 'enter' && pressed === 'return') return true;
  return false;
}

/**
 * Register hotkeys on the window (or a scope element). Handlers are held in a
 * ref, so a changing closure never re-binds the listener.
 */
export function useHotkeys(
  bindings: Hotkey[],
  opts: { enabled?: boolean; target?: RefObject<HTMLElement | null> } = {},
): void {
  const { enabled = true, target } = opts;
  const ref = useRef(bindings);
  useEffect(() => {
    ref.current = bindings;
  });

  useEffect(() => {
    if (!enabled) return;
    const el: HTMLElement | Window = target?.current ?? window;
    const onKey = (ev: Event) => {
      const e = ev as KeyboardEvent;
      const editable = isEditableTarget(e.target);
      for (const b of ref.current) {
        if (editable && !b.allowInInput) continue;
        if (matches(e, b.combo)) {
          if (b.preventDefault !== false) e.preventDefault();
          b.handler(e);
          return;
        }
      }
    };
    el.addEventListener('keydown', onKey as EventListener);
    return () => el.removeEventListener('keydown', onKey as EventListener);
  }, [enabled, target]);
}
