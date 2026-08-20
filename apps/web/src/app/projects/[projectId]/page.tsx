"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState } from "react";

import { Form, Input, Select } from "@/components/form";
import { Card, Empty, Status } from "@/components/ui";
import { api } from "@/lib/api";
import type {
  GeneratorPool,
  Page as Paged,
  PerformanceTest,
  Run,
  ScriptRepo,
  VerifyReport,
} from "@/lib/types";

function Repositories({ projectId }: { projectId: string }) {
  const queries = useQueryClient();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [planPath, setPlanPath] = useState("");
  const [ref, setRef] = useState("main");
  const [report, setReport] = useState<VerifyReport | null>(null);

  const repos = useQuery({
    queryKey: ["repos", projectId],
    queryFn: () => api.get<Paged<ScriptRepo>>(`/api/v1/projects/${projectId}/script-repos`),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<ScriptRepo>(`/api/v1/projects/${projectId}/script-repos`, {
        name,
        repoUrl: url,
        planPath,
        defaultRef: ref,
      }),
    onSuccess: () => {
      setName("");
      setUrl("");
      setPlanPath("");
      queries.invalidateQueries({ queryKey: ["repos", projectId] });
    },
  });

  const verify = useMutation({
    mutationFn: (repoId: string) =>
      api.post<VerifyReport>(`/api/v1/script-repos/${repoId}/verify`),
    onSuccess: (result) => setReport(result),
  });

  return (
    <Card title="Script repositories">
      {repos.data && repos.data.items.length > 0 ? (
        <ul className="mb-6 divide-y divide-line">
          {repos.data.items.map((repo) => (
            <li key={repo.id} className="flex items-baseline justify-between gap-4 py-2">
              <div>
                <span className="font-mono text-sm text-slate-200">{repo.name}</span>
                <span className="ml-3 font-mono text-xs text-muted">{repo.planPath}</span>
              </div>
              <button
                className="rounded border border-line px-2 py-1 text-xs text-slate-300 hover:border-accent"
                onClick={() => verify.mutate(repo.id)}
                disabled={verify.isPending}
              >
                {verify.isPending ? "Verifying…" : "Verify"}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>No repositories yet.</Empty>
      )}

      {report ? (
        <div className="mb-6 rounded border border-line p-3">
          <div className="flex items-center gap-2">
            <Status value={report.ok ? "PASS" : "FAIL"} />
            <span className="text-sm text-slate-300">
              {report.ok ? "The repository verifies clean." : "The repository has findings."}
            </span>
          </div>
          {/* Every finding, not the first: fixing them one round-trip at a
              time is what verify exists to avoid. */}
          {report.findings.length > 0 ? (
            <ul className="mt-2 space-y-1 font-mono text-xs text-muted">
              {report.findings.map((finding) => (
                <li key={`${finding.code}-${finding.message}`}>
                  {finding.severity} {finding.code}: {finding.message}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <Form submit="Connect repository" onSubmit={() => create.mutateAsync().then(() => undefined)}>
        <Input label="Name" value={name} onChange={setName} placeholder="Checkout plans" />
        <Input
          label="Repository URL"
          value={url}
          onChange={setUrl}
          placeholder="https://github.com/acme/perf-plans.git"
        />
        <Input
          label="Plan path"
          value={planPath}
          onChange={setPlanPath}
          placeholder="perf/checkout.jmx"
          hint="Path to the .jmx inside the repository. Data files beside it come with it."
        />
        <Input label="Default ref" value={ref} onChange={setRef} />
      </Form>
    </Card>
  );
}

function Tests({ projectId }: { projectId: string }) {
  const queries = useQueryClient();
  const router = useRouter();
  const [name, setName] = useState("");
  const [users, setUsers] = useState("10");
  const [duration, setDuration] = useState("60");
  const [ramp, setRamp] = useState("10");
  const [repoId, setRepoId] = useState("");
  const [poolId, setPoolId] = useState("");

  const tests = useQuery({
    queryKey: ["tests", projectId],
    queryFn: () => api.get<Paged<PerformanceTest>>(`/api/v1/projects/${projectId}/tests`),
  });
  const repos = useQuery({
    queryKey: ["repos", projectId],
    queryFn: () => api.get<Paged<ScriptRepo>>(`/api/v1/projects/${projectId}/script-repos`),
  });
  const pools = useQuery({
    queryKey: ["pools"],
    queryFn: () => api.get<Paged<GeneratorPool>>("/api/v1/generator-pools?limit=100"),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<PerformanceTest>(`/api/v1/projects/${projectId}/tests`, {
        name,
        configuration: {
          virtualUsers: Number(users),
          durationSeconds: Number(duration),
          rampUpSeconds: Number(ramp),
          generatorPoolId: poolId || pools.data?.items[0]?.id,
        },
        plans: [
          {
            scriptRepoId: repoId || repos.data?.items[0]?.id,
            // The plans' virtual users must add up to the workload's, which
            // preflight checks. One plan means it carries all of them.
            virtualUsers: Number(users),
            executionOrder: 1,
          },
        ],
        slaRules: [],
      }),
    onSuccess: () => {
      setName("");
      queries.invalidateQueries({ queryKey: ["tests", projectId] });
    },
  });

  const start = useMutation({
    mutationFn: (testId: string) => api.post<Run>(`/api/v1/tests/${testId}/runs`),
    onSuccess: (run) => router.push(`/runs/${run.id}`),
  });

  const ready = (repos.data?.items.length ?? 0) > 0 && (pools.data?.items.length ?? 0) > 0;

  return (
    <Card title="Performance tests">
      {tests.data && tests.data.items.length > 0 ? (
        <ul className="mb-6 divide-y divide-line">
          {tests.data.items.map((test) => (
            <li key={test.id} className="flex items-baseline justify-between gap-4 py-2">
              <span className="font-mono text-sm text-slate-200">{test.name}</span>
              <button
                className="rounded border border-line px-2 py-1 text-xs text-slate-300 hover:border-accent disabled:opacity-50"
                onClick={() => start.mutate(test.id)}
                disabled={start.isPending}
              >
                {start.isPending ? "Starting…" : "Run"}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>No tests yet.</Empty>
      )}

      {start.isError ? (
        <div className="mb-6 rounded border border-fail/40 bg-fail/10 p-3 text-sm text-fail">
          <p>{(start.error as Error).message}</p>
          {/* Preflight refuses the whole run and names every failing check, so
              all of them are shown rather than the first. */}
          <ul className="mt-2 space-y-1 font-mono text-xs">
            {(start.error as { checks?: () => string[] }).checks?.().map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {ready ? (
        <Form submit="Create test" onSubmit={() => create.mutateAsync().then(() => undefined)}>
          <Input label="Name" value={name} onChange={setName} placeholder="Checkout at 500 users" />
          <div className="grid grid-cols-3 gap-3">
            <Input label="Virtual users" value={users} onChange={setUsers} type="number" />
            <Input label="Duration (s)" value={duration} onChange={setDuration} type="number" />
            <Input label="Ramp-up (s)" value={ramp} onChange={setRamp} type="number" />
          </div>
          <Select
            label="Plan"
            value={repoId || (repos.data?.items[0]?.id ?? "")}
            onChange={setRepoId}
            options={(repos.data?.items ?? []).map((repo) => ({
              value: repo.id,
              label: `${repo.name} · ${repo.planPath}`,
            }))}
          />
          <Select
            label="Generator pool"
            value={poolId || (pools.data?.items[0]?.id ?? "")}
            onChange={setPoolId}
            options={(pools.data?.items ?? []).map((pool) => ({
              value: pool.id,
              label: `${pool.name} · up to ${pool.capacity} users`,
            }))}
          />
        </Form>
      ) : (
        <Empty>Connect a repository first, and make sure a generator pool exists.</Empty>
      )}
    </Card>
  );
}

export default function ProjectPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);

  return (
    <main className="space-y-6">
      <div>
        <Link className="text-sm text-accent hover:underline" href="/projects">
          ← Projects
        </Link>
        <h1 className="mt-1 text-xl font-semibold text-slate-100">Project</h1>
      </div>
      <Repositories projectId={projectId} />
      <Tests projectId={projectId} />
    </main>
  );
}
