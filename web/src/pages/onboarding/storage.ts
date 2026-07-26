/**
 * First-run tour persistence. The tour fires once per browser; the "seen" flag
 * lives in localStorage so it survives reloads but is scoped to this device — the
 * same posture as the API token and theme override. "Replay tour" in Settings
 * clears it and restarts, so the walkthrough is never lost for good.
 */

const SEEN_KEY = 'netadmin_tour_seen';

export function hasSeenTour(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) === '1';
  } catch {
    // Storage unavailable → treat as unseen so first-run guidance still shows,
    // rather than silently suppressing it.
    return false;
  }
}

export function markTourSeen(): void {
  try {
    localStorage.setItem(SEEN_KEY, '1');
  } catch {
    /* storage may be unavailable; the in-session run still completed */
  }
}

export function resetTourSeen(): void {
  try {
    localStorage.removeItem(SEEN_KEY);
  } catch {
    /* ignore */
  }
}
