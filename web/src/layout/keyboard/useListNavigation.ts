import {
  type KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useRef,
  useState,
} from 'react';
import { isEditableTarget } from './useHotkeys';

/**
 * Row-traversal primitive (docs §Interaction: "arrows/j-k traverse rows, Enter
 * opens"). Framework for any list/table: tracks an active index, moves it with
 * j/k and Arrow Up/Down, and fires `onActivate` on Enter. Reusable by DataTable
 * and the issue/device/client lists the page agents build next.
 *
 * Roving-focus friendly: the active row is scrolled into view; the caller wires
 * `getRowProps(i)` onto each row for `aria-selected` + a scroll ref.
 */

export interface ListNavigation {
  activeIndex: number;
  setActiveIndex: (i: number) => void;
  /** Spread onto the scroll container: keydown handling + focusability. */
  containerProps: {
    tabIndex: number;
    role: string;
    onKeyDown: (e: ReactKeyboardEvent) => void;
  };
  /** Spread onto each row. */
  getRowProps: (i: number) => {
    ref: (el: HTMLElement | null) => void;
    'aria-selected': boolean;
    'data-active': boolean | undefined;
    onMouseEnter: () => void;
  };
}

export function useListNavigation(
  count: number,
  onActivate?: (index: number) => void,
  opts: { wrap?: boolean; initialIndex?: number } = {},
): ListNavigation {
  const { wrap = false, initialIndex = -1 } = opts;
  const [activeIndex, setActive] = useState(initialIndex);
  const rows = useRef<(HTMLElement | null)[]>([]);

  // No clamp effect: moves self-correct out-of-range indices (a shrunk list
  // just highlights nothing until the next keypress), which avoids a
  // setState-in-effect cascade.
  const move = useCallback(
    (delta: number) => {
      setActive((prev) => {
        let next = prev < 0 ? (delta > 0 ? 0 : count - 1) : prev + delta;
        if (next < 0) next = wrap ? count - 1 : 0;
        if (next > count - 1) next = wrap ? 0 : count - 1;
        rows.current[next]?.scrollIntoView({ block: 'nearest' });
        return next;
      });
    },
    [count, wrap],
  );

  const onKeyDown = useCallback(
    (e: ReactKeyboardEvent) => {
      if (isEditableTarget(e.target)) return;
      const k = e.key.toLowerCase();
      if (k === 'j' || k === 'arrowdown') {
        e.preventDefault();
        move(1);
      } else if (k === 'k' || k === 'arrowup') {
        e.preventDefault();
        move(-1);
      } else if (k === 'enter') {
        if (activeIndex >= 0 && activeIndex < count) {
          e.preventDefault();
          onActivate?.(activeIndex);
        }
      }
    },
    [move, activeIndex, count, onActivate],
  );

  const getRowProps = useCallback(
    (i: number) => ({
      ref: (el: HTMLElement | null) => {
        rows.current[i] = el;
      },
      'aria-selected': i === activeIndex,
      'data-active': i === activeIndex ? true : undefined,
      onMouseEnter: () => setActive(i),
    }),
    [activeIndex],
  );

  return {
    activeIndex,
    setActiveIndex: setActive,
    containerProps: { tabIndex: 0, role: 'listbox', onKeyDown },
    getRowProps,
  };
}
