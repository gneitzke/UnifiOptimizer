import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useHealth } from '../../api';
import { Coachmark } from '../../components/ui/Coachmark';
import { useHotkeys } from '../../layout/keyboard/useHotkeys';
import { TOUR_STEPS } from './steps';
import { hasSeenTour, markTourSeen } from './storage';
import { useTourStore } from './tourStore';

/**
 * First-run guided tour (docs §Interaction; in-app-help pattern). Mounted once at
 * the app root inside the authed subtree, so it only runs after a token is
 * accepted. On first run it auto-starts; it is re-runnable from Settings via the
 * shared tour store.
 *
 * The runner is keyed by the store nonce, so a replay remounts it cleanly at
 * step one with no reset bookkeeping. Each step navigates to its route, resolves
 * the first matching target (centred fallback when none is present yet), and
 * hands geometry to the Coachmark. Fully keyboard-driven — arrows advance and
 * retreat, Esc skips — and it honours prefers-reduced-motion.
 */

/** How long to keep polling for a step's primary (highest-priority) target
 *  before accepting a lower-priority fallback that is already on the page. Covers
 *  an async data fetch (issues/devices tables) landing after first paint. */
const PRIMARY_WAIT_MS = 1200;
/** Absolute give-up: if nothing at all resolves by here, centre the popover. */
const GIVE_UP_MS = 1600;

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
  );
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!mq) return;
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener?.('change', onChange);
    return () => mq.removeEventListener?.('change', onChange);
  }, []);
  return Boolean(reduced);
}

export function GuidedTour() {
  const running = useTourStore((s) => s.running);
  const nonce = useTourStore((s) => s.nonce);
  const stop = useTourStore((s) => s.stop);
  const start = useTourStore((s) => s.start);

  // Auto-start on first run only, and only once the daemon actually has something
  // to show. A fresh connect lands on an empty "collecting now" dashboard whose
  // health and SLE cards read "No data" for the first few minutes; auto-launching
  // the tour over that describes state the user cannot see. Gate on the inventory
  // being populated (entities present), so the tour waits for a real dashboard —
  // this session once data arrives, or the next visit. It stays replayable from
  // Settings meanwhile.
  const health = useHealth(30_000);
  const entityCount = Number(health.data?.entities?.total ?? 0);
  const hasData = Number.isFinite(entityCount) && entityCount > 0;
  useEffect(() => {
    if (hasSeenTour() || !hasData) return;
    const t = window.setTimeout(() => {
      if (!hasSeenTour()) start();
    }, 700);
    return () => window.clearTimeout(t);
  }, [start, hasData]);

  const close = useCallback(() => {
    markTourSeen();
    stop();
  }, [stop]);

  if (!running) return null;
  // Keyed by nonce: a replay remounts the runner fresh at step one.
  return <TourRunner key={nonce} onClose={close} />;
}

function TourRunner({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const reducedMotion = usePrefersReducedMotion();

  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const targetElRef = useRef<HTMLElement | null>(null);

  const step = TOUR_STEPS[index];
  const isLast = index === TOUR_STEPS.length - 1;

  const next = useCallback(() => {
    if (index >= TOUR_STEPS.length - 1) onClose();
    else setIndex((i) => i + 1);
  }, [index, onClose]);

  const back = useCallback(() => setIndex((i) => Math.max(0, i - 1)), []);

  // Lock body scroll while the tour owns the screen (the page's own scroll
  // container still scrolls, so targets can be brought into view).
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // Ensure we are on the step's route.
  useEffect(() => {
    if (location.pathname !== step.route) navigate(step.route);
  }, [step.route, location.pathname, navigate]);

  // Resolve and spotlight the step's target once we are on its route.
  useEffect(() => {
    if (location.pathname !== step.route) return;

    let raf = 0;
    let settleTimer = 0;
    let cancelled = false;
    const started = performance.now();
    targetElRef.current = null;

    const tick = () => {
      if (cancelled) return;
      const elapsed = performance.now() - started;

      // Find the highest-priority target currently on the page.
      let el: HTMLElement | null = null;
      let matchIndex = -1;
      for (let i = 0; i < step.targets.length; i++) {
        const found = document.querySelector<HTMLElement>(step.targets[i]);
        if (found && found.getBoundingClientRect().width > 0) {
          el = found;
          matchIndex = i;
          break;
        }
      }

      // Accept the primary target the instant it appears; keep polling for it
      // before settling for a lower-priority fallback. The fallbacks (nav links)
      // are always present from the first frame, so without this a step whose
      // real target loads async — the issues/devices table rows — would lock the
      // spotlight onto its nav-link fallback before the table ever rendered.
      const accept = el !== null && (matchIndex === 0 || elapsed >= PRIMARY_WAIT_MS);
      if (accept && el) {
        targetElRef.current = el;
        el.scrollIntoView({
          behavior: reducedMotion ? 'auto' : 'smooth',
          block: 'center',
          inline: 'nearest',
        });
        setRect(el.getBoundingClientRect());
        // Re-read after smooth scroll settles so the ring lands accurately.
        settleTimer = window.setTimeout(
          () => {
            if (!cancelled && targetElRef.current) {
              setRect(targetElRef.current.getBoundingClientRect());
            }
          },
          reducedMotion ? 0 : 280,
        );
        return;
      }
      if (elapsed > GIVE_UP_MS) {
        targetElRef.current = null;
        setRect(null); // graceful centred fallback — no target ever resolved
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      window.clearTimeout(settleTimer);
    };
  }, [index, step.route, step.targets, location.pathname, reducedMotion]);

  // Keep the spotlight on the target as the page scrolls or the window resizes.
  useEffect(() => {
    const reposition = () => {
      if (targetElRef.current) setRect(targetElRef.current.getBoundingClientRect());
    };
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    return () => {
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
    };
  }, []);

  // Keyboard model: arrows traverse, Esc skips. Enter is handled by the focused
  // primary button, so it is intentionally not bound here (no double-advance).
  useHotkeys([
    { combo: 'escape', handler: onClose, allowInInput: true },
    { combo: 'arrowright', handler: next },
    { combo: 'arrowleft', handler: back },
  ]);

  if (!step) return null;

  return (
    <Coachmark
      rect={rect}
      title={step.title}
      body={step.body}
      stepIndex={index}
      stepCount={TOUR_STEPS.length}
      isLast={isLast}
      reducedMotion={reducedMotion}
      onBack={back}
      onNext={next}
      onSkip={onClose}
    />
  );
}
