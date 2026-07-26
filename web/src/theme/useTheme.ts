import { create } from 'zustand';

/**
 * Theme model per docs/DESIGN_FOUNDATION.md: prefers-color-scheme is the default,
 * an explicit toggle overrides it via the [data-theme] attribute.
 *
 * - No stored override  → follow the OS (no attribute set; CSS media query wins).
 * - Stored override     → force that theme (attribute set; wins by specificity).
 *
 * `theme` is the *effective* theme, used only to render the correct toggle icon;
 * the actual paint is driven entirely by CSS variables keyed off [data-theme].
 */

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'netadmin_theme';

const media = (): MediaQueryList | null =>
  typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null;

function systemTheme(): Theme {
  return media()?.matches ? 'dark' : 'light';
}

function storedOverride(): Theme | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === 'light' || v === 'dark' ? v : null;
  } catch {
    return null;
  }
}

/** Reflect the current override onto <html data-theme>; remove it to follow OS. */
function applyAttribute(override: Theme | null): void {
  const root = document.documentElement;
  if (override) root.setAttribute('data-theme', override);
  else root.removeAttribute('data-theme');
}

interface ThemeState {
  /** Effective theme, for the toggle icon. */
  theme: Theme;
  /** Explicit user choice, or null when following the OS. */
  override: Theme | null;
  /** Flip to the opposite of what is currently showing, and persist it. */
  toggle: () => void;
  /** Drop the override and follow the OS again. */
  useSystem: () => void;
  /** Called by the media-query listener when following the OS. */
  syncSystem: () => void;
}

const initialOverride = storedOverride();
applyAttribute(initialOverride);

export const useTheme = create<ThemeState>()((set, get) => {
  const m = media();
  if (m) {
    const onChange = () => {
      if (get().override === null) get().syncSystem();
    };
    m.addEventListener?.('change', onChange);
  }

  return {
    theme: initialOverride ?? systemTheme(),
    override: initialOverride,

    toggle: () => {
      const next: Theme = get().theme === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        /* storage may be unavailable; the attribute still applies */
      }
      applyAttribute(next);
      set({ theme: next, override: next });
    },

    useSystem: () => {
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch {
        /* ignore */
      }
      applyAttribute(null);
      set({ theme: systemTheme(), override: null });
    },

    syncSystem: () => set({ theme: systemTheme() }),
  };
});
