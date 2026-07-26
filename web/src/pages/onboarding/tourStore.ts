import { create } from 'zustand';

/**
 * Tour run-state, shared between the runner (GuidedTour, mounted at the app root)
 * and any trigger (the first-run hook, or "Replay tour" in Settings). Kept
 * deliberately tiny: whether the tour is on screen, and a nonce that lets a
 * replay restart cleanly from step one even if the tour was already open.
 */

interface TourState {
  running: boolean;
  /** Bumped on every start(), so the runner can reset its step index. */
  nonce: number;
  start: () => void;
  stop: () => void;
}

export const useTourStore = create<TourState>()((set) => ({
  running: false,
  nonce: 0,
  start: () => set((s) => ({ running: true, nonce: s.nonce + 1 })),
  stop: () => set({ running: false }),
}));

/** Imperative trigger for non-React callers / one-liners. */
export function startTour(): void {
  useTourStore.getState().start();
}
