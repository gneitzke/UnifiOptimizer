import { useCallback, useMemo, useRef, type ReactNode } from 'react';
import { FilterFocusContext } from './filterFocusContext';

/**
 * Provides the `/`-focuses-the-filter plumbing (docs §Interaction). A view
 * registers its filter box with `useRegisterFilter()` (from ./filterFocusContext);
 * the shell owns the global `/` hotkey and calls `focusFilter()`. Decoupled so
 * any page can opt in without the shell knowing which input it is.
 */
export function FilterFocusProvider({ children }: { children: ReactNode }) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  const register = useCallback((el: HTMLInputElement | null) => {
    inputRef.current = el;
  }, []);

  const focusFilter = useCallback(() => {
    const el = inputRef.current;
    if (el) {
      el.focus();
      el.select?.();
      return true;
    }
    return false;
  }, []);

  const value = useMemo(() => ({ register, focusFilter }), [register, focusFilter]);

  return (
    <FilterFocusContext.Provider value={value}>
      {children}
    </FilterFocusContext.Provider>
  );
}
