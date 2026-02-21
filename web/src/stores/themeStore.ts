import { create } from 'zustand';

type Theme = 'dark' | 'light';

interface ThemeState {
  theme: Theme;
  toggleTheme: () => void;
}

function applyTheme(t: Theme) {
  document.documentElement.setAttribute(
    'data-theme',
    t,
  );
  localStorage.setItem('unifi_theme', t);
}

const stored =
  (localStorage.getItem('unifi_theme') as Theme) ??
  'dark';
applyTheme(stored);

export const useThemeStore = create<ThemeState>()(
  (set) => ({
    theme: stored,

    toggleTheme: () =>
      set((s) => {
        const next =
          s.theme === 'dark' ? 'light' : 'dark';
        applyTheme(next);
        return { theme: next };
      }),
  }),
);
