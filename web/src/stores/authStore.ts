import { create } from 'zustand';
import * as api from '../services/api';
import type { LoginRequest } from '../types/api';

interface AuthState {
  token: string | null;
  isAuthenticated: boolean;
  host: string;
  username: string;
  site: string;
  isLoading: boolean;

  login: (req: LoginRequest) => Promise<void>;
  logout: () => Promise<void>;
  validate: () => Promise<void>;
  setToken: (t: string | null) => void;
}

export const useAuthStore = create<AuthState>()(
  (set, get) => ({
    token: localStorage.getItem('unifi_token'),
    isAuthenticated: false,
    host: '',
    username: '',
    site: 'default',
    isLoading: !!localStorage.getItem('unifi_token'),

    setToken: (t) => {
      api.setToken(t);
      set({
        token: t,
        isAuthenticated: !!t,
      });
    },

    login: async (req) => {
      set({ isLoading: true });
      try {
        const res = await api.login(req);
        set({
          token: res.token,
          isAuthenticated: true,
          host: res.host,
          username: res.username,
          site: res.site,
          isLoading: false,
        });
      } catch (err) {
        set({ isLoading: false });
        throw err;
      }
    },

    logout: async () => {
      await api.logout();
      set({
        token: null,
        isAuthenticated: false,
        host: '',
        username: '',
        site: '',
      });
    },

    validate: async () => {
      set({ isLoading: true });
      try {
        const s = await api.validate();
        if (s.authenticated) {
          set({
            isAuthenticated: true,
            host: s.host ?? '',
            username: s.username ?? '',
            site: s.site ?? 'default',
            isLoading: false,
          });
        } else {
          api.setToken(null);
          set({
            token: null,
            isAuthenticated: false,
            isLoading: false,
          });
        }
      } catch (err) {
        // Network errors shouldn't log you out — only invalid tokens should
        const msg =
          err instanceof Error ? err.message : '';
        const isAuthError =
          msg === 'Unauthorized' || msg.includes('401');
        set({
          isAuthenticated: isAuthError ? false : get().isAuthenticated,
          isLoading: false,
        });
      }
    },
  }),
);
