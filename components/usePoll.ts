"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type PollResult<T> = {
  data: T | null;
  error: string | null;
  status: number | null;
  loading: boolean;
  refresh: () => Promise<void>;
};

export function usePoll<T>(url: string, intervalMs = 1500): PollResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const response = await fetch(url, { cache: "no-store" });
      setStatus(response.status);
      if (!response.ok) {
        if (response.status === 401) {
          setData(null);
          setError(null);
          return;
        }
        const payload = (await response.json().catch(() => null)) as { error?: string } | null;
        setError(payload?.error ?? `Request failed (${response.status}).`);
        return;
      }
      const payload = (await response.json()) as T;
      setData(payload);
      setError(null);
    } catch {
      setError("Connection lost. Retrying...");
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [url]);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      if (!cancelled && !document.hidden) void refresh();
    };
    void refresh();
    const timer = setInterval(tick, intervalMs);
    const onVisible = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh, intervalMs]);

  return { data, error, status, loading, refresh };
}
