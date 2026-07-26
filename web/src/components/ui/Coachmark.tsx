import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react';
import { ArrowLeft, ArrowRight, X } from 'lucide-react';
import { Button } from './Button';

/**
 * Spotlight coachmark (docs §Interaction; onboarding tour primitive). Presentational
 * only: given a target rect it dims the page with the shared --overlay token,
 * cuts a soft hole around the target with a single accent ring, and floats a
 * quiet popover beside it. With no rect it centres the popover over a plain scrim.
 *
 * Restraint: one accent ring (never-do rule 1), no glow/gradient (rule 10), both
 * themes via tokens. It never traps the user — Esc and Skip are always live, and
 * a click-blocker only prevents *accidental* interaction with the page behind,
 * exactly like the command palette's scrim.
 *
 * The controller (GuidedTour) owns step data, routing, and target resolution;
 * this component owns geometry and focus only.
 */

/** Padding of the cut-out hole around the target, px. */
const HOLE_PAD = 8;
/** Gap between the target and the popover, px. */
const GAP = 14;
/** Viewport margin the popover keeps from every edge, px. */
const MARGIN = 16;
const POP_W = 340;

type Side = 'top' | 'bottom' | 'left' | 'right' | 'center';

interface Placement {
  top: number;
  left: number;
  side: Side;
}

function placePopover(
  rect: DOMRect | null,
  popH: number,
  vw: number,
  vh: number,
): Placement {
  if (!rect) {
    return {
      top: Math.max(MARGIN, (vh - popH) / 2),
      left: Math.max(MARGIN, (vw - POP_W) / 2),
      side: 'center',
    };
  }

  const clampLeft = (l: number) =>
    Math.min(Math.max(MARGIN, l), vw - POP_W - MARGIN);
  const clampTop = (t: number) => Math.min(Math.max(MARGIN, t), vh - popH - MARGIN);
  // Centre the popover on the target's mid-line for the chosen axis.
  const alignX = clampLeft(rect.left + rect.width / 2 - POP_W / 2);
  const alignY = clampTop(rect.top + rect.height / 2 - popH / 2);

  const below = rect.bottom + GAP;
  if (below + popH <= vh - MARGIN) {
    return { top: below, left: alignX, side: 'bottom' };
  }
  const above = rect.top - GAP - popH;
  if (above >= MARGIN) {
    return { top: above, left: alignX, side: 'top' };
  }
  const right = rect.right + GAP;
  if (right + POP_W <= vw - MARGIN) {
    return { top: alignY, left: right, side: 'right' };
  }
  const left = rect.left - GAP - POP_W;
  if (left >= MARGIN) {
    return { top: alignY, left, side: 'left' };
  }
  // No room on any side — centre it and drop the ring's relevance gracefully.
  return {
    top: Math.max(MARGIN, (vh - popH) / 2),
    left: clampLeft(rect.left + rect.width / 2 - POP_W / 2),
    side: 'center',
  };
}

export interface CoachmarkProps {
  rect: DOMRect | null;
  title: string;
  body: ReactNode;
  stepIndex: number;
  stepCount: number;
  isLast: boolean;
  reducedMotion: boolean;
  onBack: () => void;
  onNext: () => void;
  onSkip: () => void;
}

export function Coachmark({
  rect,
  title,
  body,
  stepIndex,
  stepCount,
  isLast,
  reducedMotion,
  onBack,
  onNext,
  onSkip,
}: CoachmarkProps) {
  const popRef = useRef<HTMLDivElement | null>(null);
  const [popH, setPopH] = useState(180);
  const [vp, setVp] = useState({ w: window.innerWidth, h: window.innerHeight });

  // Keep the popover height and viewport current so placement stays correct as
  // copy length or window size changes.
  useLayoutEffect(() => {
    if (popRef.current) setPopH(popRef.current.offsetHeight);
  }, [title, body, stepIndex, vp.w, vp.h]);

  useEffect(() => {
    const onResize = () => setVp({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Move focus to the primary action on each step; a step change re-runs this.
  useEffect(() => {
    const id = requestAnimationFrame(() =>
      popRef.current?.querySelector<HTMLElement>('[data-primary]')?.focus(),
    );
    return () => cancelAnimationFrame(id);
  }, [stepIndex]);

  // Restore focus to whatever opened the tour when it closes, so a keyboard user
  // (e.g. Settings > Replay tour) lands back on the trigger, not on <body>.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    return () => opener?.focus?.();
  }, []);

  // Trap Tab within the popover's controls so focus never leaks to the page.
  const onKeyDown = useCallback((e: ReactKeyboardEvent) => {
    if (e.key !== 'Tab') return;
    const root = popRef.current;
    if (!root) return;
    const focusable = Array.from(
      root.querySelectorAll<HTMLElement>('button:not([disabled])'),
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement as HTMLElement | null;
    if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }, []);

  const place = placePopover(rect, popH, vp.w, vp.h);
  const motion = reducedMotion ? 'none' : 'top 220ms ease, left 220ms ease, width 220ms ease, height 220ms ease';

  return (
    <div className="fixed inset-0 z-[60]" role="dialog" aria-modal="true" aria-label="Product tour">
      {/* Click-blocker: prevents accidental interaction with the page behind,
          never dismisses (Esc / Skip only), so the user is guided, not trapped. */}
      <div className="absolute inset-0" style={{ pointerEvents: 'auto' }} aria-hidden />

      {rect ? (
        // Spotlight: a single element whose huge box-shadow paints the scrim
        // everywhere except its own rounded hole.
        <div
          aria-hidden
          className="absolute"
          style={{
            top: rect.top - HOLE_PAD,
            left: rect.left - HOLE_PAD,
            width: rect.width + HOLE_PAD * 2,
            height: rect.height + HOLE_PAD * 2,
            borderRadius: 12,
            boxShadow: '0 0 0 9999px var(--overlay)',
            outline: '2px solid var(--accent)',
            outlineOffset: 0,
            pointerEvents: 'none',
            transition: motion,
          }}
        />
      ) : (
        <div aria-hidden className="absolute inset-0" style={{ background: 'var(--overlay)' }} />
      )}

      <div
        ref={popRef}
        onKeyDown={onKeyDown}
        className="absolute rounded-card p-4 flex flex-col gap-3"
        style={{
          top: place.top,
          left: place.left,
          width: POP_W,
          maxWidth: `calc(100vw - ${MARGIN * 2}px)`,
          background: 'var(--elevated)',
          border: '1px solid var(--hairline)',
          boxShadow: 'var(--shadow-elevated)',
          pointerEvents: 'auto',
          transition: motion,
        }}
      >
        <div className="flex items-start justify-between gap-3">
          <h2 className="t-section" style={{ color: 'var(--fg)' }} aria-live="polite">
            {title}
          </h2>
          <button
            type="button"
            onClick={onSkip}
            aria-label="Skip tour"
            className="inline-flex items-center justify-center w-7 h-7 -mr-1 -mt-0.5 rounded-control cursor-pointer transition-colors hover:bg-canvas shrink-0"
            style={{ color: 'var(--fg-subtle)' }}
          >
            <X size={16} />
          </button>
        </div>

        <p className="t-secondary" style={{ color: 'var(--fg-muted)' }} aria-live="polite">
          {body}
        </p>

        <div className="flex items-center justify-between gap-3 pt-1">
          <span className="t-caption tnum" style={{ color: 'var(--fg-subtle)' }}>
            {stepIndex + 1} / {stepCount}
          </span>
          <div className="flex items-center gap-2">
            {stepIndex > 0 && (
              <Button variant="ghost" size="sm" onClick={onBack}>
                <ArrowLeft size={14} />
                Back
              </Button>
            )}
            <Button data-primary variant="primary" size="sm" onClick={onNext}>
              {isLast ? 'Done' : 'Next'}
              {!isLast && <ArrowRight size={14} />}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
