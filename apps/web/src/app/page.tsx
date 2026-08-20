"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Card, Empty, Status } from "@/components/ui";
import { api, readToken, signIn } from "@/lib/api";
import type { Page, Project, Run } from "@/lib/types";

function SignIn({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("admin@demo.plimsoll.dev");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  return (
    <form
      className="mx-auto mt-24 max-w-sm space-y-4"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        setError(null);
        try {
          await signIn(email, password);
          onDone();
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : "Sign-in failed.");
        } finally {
          setBusy(false);
        }
      }}
    >
      <h1 className="text-xl font-semibold text-slate-100">Plimsoll</h1>
      <label className="block text-sm text-muted">
        Email
        <input
          className="mt-1 w-full rounded border border-line bg-ink px-3 py-2 font-mono text-sm text-slate-200"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          autoComplete="username"
        />
      </label>
      <label className="block text-sm text-muted">
        Password
        <input
          className="mt-1 w-full rounded border border-line bg-ink px-3 py-2 font-mono text-sm text-slate-200"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
        />
      </label>
      {error ? <p className="text-sm text-fail">{error}</p> : null}
      <button
        className="w-full rounded bg-accent px-3 py-2 text-sm font-medium text-ink disabled:opacity-50"
        disabled={busy}
      >
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

function Runs() {
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Page<Project>>("/api/v1/projects?limit=50"),
  });

  const runs = useQuery({
    queryKey: ["runs", projects.data?.items.map((item) => item.id)],
    enabled: Boolean(projects.data),
    queryFn: async () => {
      const pages = await Promise.all(
        (projects.data?.items ?? []).map((project) =>
          api.get<Page<Run>>(`/api/v1/projects/${project.id}/runs?limit=10`),
        ),
      );
      return pages
        .flatMap((page) => page.items)
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
        .slice(0, 30);
    },
    // A list of runs goes stale the moment one of them moves.
    refetchInterval: 5_000,
  });

  if (projects.isError) return <Empty>Could not reach the API.</Empty>;
  if (!runs.data) return <Empty>Loading…</Empty>;
  if (runs.data.length === 0) return <Empty>No runs yet.</Empty>;

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-xs uppercase tracking-wide text-muted">
          <th className="pb-2">Run</th>
          <th className="pb-2">Status</th>
          <th className="pb-2">SLA</th>
          <th className="pb-2">Started</th>
        </tr>
      </thead>
      <tbody className="font-mono">
        {runs.data.map((run) => (
          <tr key={run.id} className="border-t border-line">
            <td className="py-2">
              <Link className="text-accent hover:underline" href={`/runs/${run.id}`}>
                #{run.runNumber}
              </Link>
              {run.degraded ? <span className="ml-2 text-xs text-warn">degraded</span> : null}
            </td>
            <td className="py-2">
              <Status value={run.status} />
            </td>
            <td className="py-2">{run.slaResult ? <Status value={run.slaResult} /> : "—"}</td>
            <td className="py-2 text-muted">
              {run.startedAt ? new Date(run.startedAt).toLocaleTimeString() : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Home() {
  const router = useRouter();
  const [signedIn, setSignedIn] = useState<boolean | null>(null);

  useEffect(() => setSignedIn(Boolean(readToken())), []);

  if (signedIn === null) return null;
  if (!signedIn) return <SignIn onDone={() => { setSignedIn(true); router.refresh(); }} />;

  return (
    <main className="space-y-6">
      <h1 className="text-xl font-semibold text-slate-100">Runs</h1>
      <Card>
        <Runs />
      </Card>
    </main>
  );
}
