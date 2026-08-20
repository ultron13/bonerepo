"use client";

import { useEffect, useRef, useState } from "react";

import { apiBase, readToken } from "./api";
import type { LiveEvent, MetricEvent, RunStatus } from "./types";

export interface LiveWindow {
  transaction: string;
  windowStart: string;
  count: number;
  errorCount: number;
  p50: number;
  p95: number;
}

/**
 * One run's live events.
 *
 * A window may be announced more than once -- samples belonging to it can be
 * read after it was drained -- and each announcement carries the running total
 * for that window rather than an increment. So windows are keyed on
 * (transaction, windowStart) and replaced, never accumulated: adding them would
 * double-count, and the dashboard would disagree with the results endpoint.
 */
export function useLiveRun(runId: string, active: boolean) {
  const [windows, setWindows] = useState<Map<string, LiveWindow>>(new Map());
  const [status, setStatus] = useState<RunStatus | null>(null);
  const socket = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!active) return;
    const token = readToken();
    if (!token) return;

    const url = `${apiBase().replace(/^http/, "ws")}/ws/runs/${runId}?token=${token}`;
    const connection = new WebSocket(url);
    socket.current = connection;

    connection.onmessage = (message) => {
      const event = JSON.parse(message.data as string) as LiveEvent;
      if (event.type === "run.status") {
        setStatus(event.status);
        return;
      }
      const metric = event as MetricEvent;
      setWindows((previous) => {
        const next = new Map(previous);
        next.set(`${metric.transaction} ${metric.windowStart}`, {
          transaction: metric.transaction,
          windowStart: metric.windowStart,
          count: Number(metric.count),
          errorCount: Number(metric.errorCount),
          p50: Number(metric.p50),
          p95: Number(metric.p95),
        });
        return next;
      });
    };

    return () => connection.close();
  }, [runId, active]);

  return { windows: [...windows.values()], status };
}
