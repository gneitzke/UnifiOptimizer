import { useCallback, useEffect, useRef, useState } from 'react';
import type { WsFrame } from './types';
import { getToken, subscribeAuth, wsTokenQuery } from './token';

/**
 * Live connection to the daemon's single WebSocket (/ws), which pushes
 * `issue_transition` frames and a 30s `heartbeat` (docs §12). The old 2s polling
 * loop is gone; this is the live channel.
 *
 * Reconnects with capped exponential backoff. `onFrame` is held in a ref so a
 * changing callback never tears down the socket, and the effect depends only on
 * `enabled`/`url` — the connection is stable across renders.
 */

export type WsStatus = 'connecting' | 'open' | 'closed';

interface Options {
  enabled?: boolean;
  onFrame?: (frame: WsFrame) => void;
}

function defaultUrl(): string {
  // The token rides as a query param (browsers cannot set WebSocket headers); the
  // daemon accepts it only when auth is configured, and ignores it otherwise.
  if (typeof window === 'undefined') return `/ws${wsTokenQuery()}`;
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}/ws${wsTokenQuery()}`;
}

const MAX_BACKOFF_MS = 15_000;
const BASE_BACKOFF_MS = 500;

export function useWebSocket(options: Options = {}) {
  const { enabled = true, onFrame } = options;
  const [status, setStatus] = useState<WsStatus>('closed');
  const [lastFrame, setLastFrame] = useState<WsFrame | null>(null);
  // Reconnect when the token VALUE changes (a fresh sign-in / sign-out), not on
  // every auth-state emit — a bare auth-required flip must not churn the socket.
  const [tokenTick, setTokenTick] = useState(0);
  const lastTokenRef = useRef(getToken());
  useEffect(
    () =>
      subscribeAuth(() => {
        const next = getToken();
        if (next !== lastTokenRef.current) {
          lastTokenRef.current = next;
          setTokenTick((t) => t + 1);
        }
      }),
    [],
  );

  const onFrameRef = useRef(onFrame);
  useEffect(() => {
    onFrameRef.current = onFrame;
  });

  const wsRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closedRef = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    closedRef.current = false;
    const url = defaultUrl();

    const clearTimer = () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };

    const connect = () => {
      if (closedRef.current) return;
      setStatus('connecting');
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        setStatus('open');
      };

      ws.onmessage = (ev) => {
        let frame: WsFrame;
        try {
          frame = JSON.parse(ev.data as string) as WsFrame;
        } catch {
          return; // ignore non-JSON frames
        }
        setLastFrame(frame);
        onFrameRef.current?.(frame);
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (closedRef.current) return;
        setStatus('closed');
        scheduleReconnect();
      };

      ws.onerror = () => {
        // onclose fires next and drives the reconnect.
        ws.close();
      };
    };

    const scheduleReconnect = () => {
      if (closedRef.current) return;
      clearTimer();
      const backoff = Math.min(
        MAX_BACKOFF_MS,
        BASE_BACKOFF_MS * 2 ** attemptRef.current,
      );
      attemptRef.current += 1;
      timerRef.current = setTimeout(connect, backoff);
    };

    connect();

    return () => {
      closedRef.current = true;
      clearTimer();
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
        ws.close();
      }
      setStatus('closed');
    };
  }, [enabled, tokenTick]);

  const send = useCallback((data: unknown) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(typeof data === 'string' ? data : JSON.stringify(data));
    }
  }, []);

  return { status, lastFrame, send };
}
