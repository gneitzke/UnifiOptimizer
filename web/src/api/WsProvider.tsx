import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from 'react';
import { useWebSocket, type WsStatus } from './useWebSocket';
import type { WsFrame } from './types';

/**
 * The single application-wide WebSocket (docs/ARCHITECTURE.md §12: "one real
 * WebSocket"). Mounted once at the shell, it owns the only `/ws` connection; the
 * shell and every page subscribe to its frame stream instead of each opening
 * their own socket (which registered N broadcaster slots and churned a socket on
 * every route change). Subscribers come and go with route mounts without ever
 * touching the connection.
 */

type FrameHandler = (frame: WsFrame) => void;

interface WsContextValue {
  status: WsStatus;
  /** Register a frame handler; returns an unsubscribe. */
  subscribe: (handler: FrameHandler) => () => void;
}

const WsContext = createContext<WsContextValue | null>(null);

export function WsProvider({ children }: { children: ReactNode }) {
  // A stable Set of handlers; the socket's onFrame fans out to all of them. Held
  // in a ref so the useWebSocket effect never tears down when subscribers change.
  const subsRef = useRef<Set<FrameHandler>>(new Set());

  const { status } = useWebSocket({
    onFrame: (frame) => {
      for (const cb of Array.from(subsRef.current)) {
        try {
          cb(frame);
        } catch {
          /* one bad subscriber must not starve the others */
        }
      }
    },
  });

  const subscribe = useCallback((handler: FrameHandler) => {
    const subs = subsRef.current;
    subs.add(handler);
    return () => {
      subs.delete(handler);
    };
  }, []);

  const value = useMemo<WsContextValue>(() => ({ status, subscribe }), [status, subscribe]);

  return <WsContext.Provider value={value}>{children}</WsContext.Provider>;
}

/** Live connection status of the one shared socket. */
export function useWsStatus(): WsStatus {
  return useContext(WsContext)?.status ?? 'closed';
}

/**
 * Subscribe to the shared socket's frames for the lifetime of the calling
 * component. The handler is held in a ref, so a changing callback never
 * re-subscribes and the subscription is stable across renders.
 */
export function useWsFrames(onFrame: FrameHandler): void {
  const ctx = useContext(WsContext);
  const handlerRef = useRef(onFrame);
  useEffect(() => {
    handlerRef.current = onFrame;
  });
  useEffect(() => {
    if (!ctx) return;
    return ctx.subscribe((frame) => handlerRef.current(frame));
  }, [ctx]);
}
