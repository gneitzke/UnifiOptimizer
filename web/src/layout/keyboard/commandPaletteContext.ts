import { createContext, useContext } from 'react';

/**
 * Command-palette context + hook, kept apart from the Provider component so each
 * file exports a single kind of thing (a clean fast-refresh boundary).
 */

export interface CommandPaletteValue {
  open: boolean;
  setOpen: (v: boolean) => void;
  toggle: () => void;
}

export const CommandPaletteContext = createContext<CommandPaletteValue | null>(null);

export function useCommandPalette(): CommandPaletteValue {
  const ctx = useContext(CommandPaletteContext);
  if (!ctx) return { open: false, setOpen: () => {}, toggle: () => {} };
  return ctx;
}
