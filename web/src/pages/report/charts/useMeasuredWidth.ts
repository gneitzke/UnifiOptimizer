import { type RefObject, useLayoutEffect, useRef, useState } from 'react';

/**
 * Width of a container, observed so the hand-rolled SVG charts fill their column
 * responsively (and reflow to the print page width). Mirrors the private hook in
 * the shared TimeSeriesChart so the report charts share one measuring behaviour.
 */
export function useMeasuredWidth(
  fallback = 640,
): [RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0]?.contentRect.width;
      if (cw && cw > 0) setW(Math.round(cw));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}
