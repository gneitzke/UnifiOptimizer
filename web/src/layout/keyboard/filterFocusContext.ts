import { createContext, useContext } from 'react';

/**
 * Filter-focus context + hooks (the `/` shortcut), kept apart from the Provider
 * component so each file exports a single kind of thing.
 */

export interface FilterFocusValue {
  register: (el: HTMLInputElement | null) => void;
  focusFilter: () => boolean;
}

export const FilterFocusContext = createContext<FilterFocusValue | null>(null);

function useFilterFocusContext(): FilterFocusValue {
  const ctx = useContext(FilterFocusContext);
  // Safe no-op outside a provider, so a page can render in isolation (tests).
  if (!ctx) return { register: () => {}, focusFilter: () => false };
  return ctx;
}

/** Callback ref a page spreads onto its filter input to claim the `/` key. */
export function useRegisterFilter(): (el: HTMLInputElement | null) => void {
  return useFilterFocusContext().register;
}

/** The shell uses this to focus whichever input is currently registered. */
export function useFocusFilter(): () => boolean {
  return useFilterFocusContext().focusFilter;
}
