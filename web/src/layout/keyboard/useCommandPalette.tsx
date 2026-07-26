import { type ReactNode, useCallback, useMemo, useState } from 'react';
import { useHotkeys } from './useHotkeys';
import { CommandPaletteContext } from './commandPaletteContext';

/**
 * Provides Cmd+K open/close state (docs §Interaction). Owns the global Cmd+K
 * binding (fires even inside inputs). The `useCommandPalette` hook lives in
 * ./commandPaletteContext.
 */
export function CommandPaletteProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((v) => !v), []);

  useHotkeys([{ combo: 'mod+k', handler: toggle, allowInInput: true }]);

  const value = useMemo(() => ({ open, setOpen, toggle }), [open, toggle]);

  return (
    <CommandPaletteContext.Provider value={value}>
      {children}
    </CommandPaletteContext.Provider>
  );
}
