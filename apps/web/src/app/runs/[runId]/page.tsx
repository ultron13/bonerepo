"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";

import { Card, Empty, Field, Status } from "@/components/ui";
import { api } from "@/lib/api";
import {
  TERMINAL,
  type Run,
  type RunErrors,
  type RunMetrics,
  type RunStatusResponse,
} from "@/lib/types";
import { useLiveRun, type LiveWindow } from "@/lib/useLiveRun";

function Live({ windows }: { windows: LiveWindow[] }) {
  if (windows.length === 0) {
    return <Empty>Waiting for the first window…</Empty>;
  }
  // Newest first: a live view answers "what is happening now".
  const recent = [...windows]
    .sort((a, b) => b.windowStart.localeCompare(a.windowStart))
    .slice(0, 12);
  return (
    <table className="w-full font-mono text-sm">
      <thead>
        <tr className="text-left text-xs uppercase tracking-wide text-muted">
          <th className="pb-2">Window</th>
          <th className="pb-2">Transaction</th>
          <th className="pb-2 text-right">n</th>
          <th className="pb-2 text-right">errors</th>
          <th className="pb-2 text-right">p50</th>
          <th className="pb-2 text-right">p95</th>
        </tr>
      </thead>
      <tbody>
        {recent.map((window) => (
          <tr key={`${window.transaction}-${window.windowStart}`} className="border-t border-line">
            <td className="py-1.5 text-muted">{window.windowStart.slice(11, 19)}</td>
            <td className="py-1.5">{window.transaction}</td>
            <td className="py-1.5 text-right">{window.count}</td>
            <td className="py-1.5 text-right">{window.errorCount}</td>
            <td className="py-1.5 text-right">{window.p50}ms</td>
            <td className="py-1.5 text-right">{window.p95}ms</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = use(params);
  const queries = useQueryClient();

  const status = useQuery({
    queryKey: ["run-status", runId],
    queryFn: () => api.get<RunStatusResponse>(`/api/v1/runs/${runId}/status`),
    refetchInterval: (query) =>
      query.state.data && TERMINAL.has(query.state.data.status) ? false : 3_000,
  });
  const running = Boolean(status.data && !TERMINAL.has(status.data.status));

  const run = useQuery({
    queryKey: ["run", runId, status.data?.status],
    queryFn: () => api.get<Run>(`/api/v1/runs/${runId}`),
  });
  const metrics = useQuery({
    queryKey: ["metrics", runId, status.data?.status],
    queryFn: () => api.get<RunMetrics>(`/api/v1/runs/${runId}/metrics`),
  });
  const errors = useQuery({
    queryKey: ["errors", runId, status.data?.status],
    queryFn: () => api.get<RunErrors>(`/api/v1/runs/${runId}/errors`),
  });

  const stop = useMutation({
    mutationFn: () => api.post(`/api/v1/runs/${runId}/stop`),
    onSuccess: () => queries.invalidateQueries({ queryKey: ["run-status", runId] }),
  });

  const live = useLiveRun(runId, running);
  const sla = run.data?.summary?.sla;

  return (
    <main className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Link className="text-sm text-accent hover:underline" href="/">
            ← Runs
          </Link>
          <h1 className="mt-1 text-xl font-semibold text-slate-100">
            Run {run.data ? `#${run.data.runNumber}` : ""}
          </h1>
        </div>
        {running ? (
          <button
            className="rounded border border-line px-3 py-1.5 text-sm text-slate-200 hover:border-accent disabled:opacity-50"
            onClick={() => stop.mutate()}
            disabled={stop.isPending}
          >
            {stop.isPending ? "Stopping…" : "Stop run"}
          </button>
        ) : null}
      </div>

      <Card>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Field label="Status">
            <Status value={status.data?.status ?? live.status ?? "…"} />
          </Field>
          <Field label="SLA">
            {run.data?.slaResult ? <Status value={run.data.slaResult} /> : "—"}
          </Field>
          <Field label="Generators">{status.data?.generators.length ?? "—"}</Field>
          <Field label="Degraded">{status.data?.degraded ? "yes" : "no"}</Field>
        </div>
        {run.data?.configurationSnapshot?.plans?.[0]?.commitSha ? (
          <p className="mt-4 text-xs text-muted">
            Pinned to commit{" "}
            <span className="font-mono">
              {run.data.configurationSnapshot.plans[0].commitSha.slice(0, 12)}
            </span>
            {run.data.configurationSnapshot.bundleSha256 ? (
              <>
                , bundle{" "}
                <span className="font-mono">
                  {run.data.configurationSnapshot.bundleSha256.slice(0, 12)}
                </span>
              </>
            ) : null}
          </p>
        ) : null}
      </Card>

      {running ? (
        <Card title="Live">
          <Live windows={live.windows} />
        </Card>
      ) : null}

      <Card title="Results">
        {metrics.data && metrics.data.transactions.length > 0 ? (
          <table className="w-full font-mono text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted">
                <th className="pb-2">Transaction</th>
                <th className="pb-2 text-right">Samples</th>
                <th className="pb-2 text-right">Errors</th>
                <th className="pb-2 text-right">p50</th>
                <th className="pb-2 text-right">p95</th>
                <th className="pb-2 text-right">p99</th>
                <th className="pb-2 text-right">TPS</th>
              </tr>
            </thead>
            <tbody>
              {metrics.data.transactions.map((item) => (
                <tr key={item.transaction} className="border-t border-line">
                  <td className="py-1.5">{item.transaction}</td>
                  <td className="py-1.5 text-right">{item.count}</td>
                  <td className="py-1.5 text-right">
                    {item.errorCount} ({(item.errorRate * 100).toFixed(2)}%)
                  </td>
                  <td className="py-1.5 text-right">{item.p50}ms</td>
                  <td className="py-1.5 text-right">{item.p95}ms</td>
                  <td className="py-1.5 text-right">{item.p99}ms</td>
                  <td className="py-1.5 text-right">{item.throughput.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No measurements yet.</Empty>
        )}
        <p className="mt-3 text-xs text-muted">
          Percentiles are computed once from HDR sketches merged across every generator, never
          averaged.
        </p>
      </Card>

      {sla && sla.rules.length > 0 ? (
        <Card title="SLA">
          <table className="w-full font-mono text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted">
                <th className="pb-2">Rule</th>
                <th className="pb-2">Verdict</th>
                <th className="pb-2 text-right">Actual</th>
                <th className="pb-2 text-right">Threshold</th>
              </tr>
            </thead>
            <tbody>
              {sla.rules.map((rule) => (
                <tr key={rule.name} className="border-t border-line">
                  <td className="py-1.5">
                    {rule.name}
                    {rule.detail ? <div className="text-xs text-muted">{rule.detail}</div> : null}
                  </td>
                  <td className="py-1.5">
                    <Status value={rule.verdict} />
                  </td>
                  <td className="py-1.5 text-right">{rule.actual ?? "—"}</td>
                  <td className="py-1.5 text-right">
                    {rule.operator} {rule.threshold}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {sla.detail ? <p className="mt-3 text-xs text-warn">{sla.detail}</p> : null}
        </Card>
      ) : null}

      <Card title={`Errors${errors.data ? ` · ${errors.data.total}` : ""}`}>
        {errors.data && errors.data.items.length > 0 ? (
          <ul className="space-y-3">
            {errors.data.items.map((group) => (
              <li
                key={group.fingerprint}
                className="border-t border-line pt-3 first:border-0 first:pt-0"
              >
                <div className="flex items-baseline justify-between gap-4">
                  <span className="font-mono text-sm text-slate-200">
                    {group.transaction} · {group.errorCode || "—"}
                  </span>
                  <span className="font-mono text-sm text-fail">×{group.count}</span>
                </div>
                <p className="mt-1 font-mono text-xs text-muted">{group.sample}</p>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No failures recorded.</Empty>
        )}
      </Card>
    </main>
  );
}
