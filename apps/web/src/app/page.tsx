"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Card, Empty, Status } from "@/components/ui";
import { api, readToken, signIn } from "@/lib/api";
import type { Me, Page, Run } from "@/lib/types";

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
  const runs = useQuery({
    queryKey: ["runs"],
    // One request for the organisation's newest runs. This used to be a page
    // of projects followed by a request per project, which got slower with
    // every project and stopped being correct well before that: past one page
    // of projects, the newest run could belong to one nobody had fetched.
    queryFn: () => api.get<Page<Run>>("/api/v1/runs?limit=30"),
    // A list of runs goes stale the moment one of them moves.
    refetchInterval: 5_000,
  });

  if (runs.isError) return <Empty>Could not reach the API.</Empty>;
  if (!runs.data) return <Empty>Loading…</Empty>;
  if (runs.data.items.length === 0) return <Empty>No runs yet.</Empty>;

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
        {runs.data.items.map((run) => (
          <tr key={run.id} className="border-t border-line">
            <td className="py-2">
              <Link
                className="text-accent hover:underline"
                href={`/runs/${run.id}`}
              >
                #{run.runNumber}
              </Link>
              {run.degraded ? (
                <span className="ml-2 text-xs text-warn">degraded</span>
              ) : null}
            </td>
            <td className="py-2">
              <Status value={run.status} />
            </td>
            <td className="py-2">
              {run.slaResult ? <Status value={run.slaResult} /> : "—"}
            </td>
            <td className="py-2 text-muted">
              {run.startedAt
                ? new Date(run.startedAt).toLocaleTimeString()
                : "—"}
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

  const me = useQuery({
    queryKey: ["me"],
    enabled: signedIn === true,
    queryFn: () => api.get<Me>("/api/v1/auth/me"),
  });

  if (signedIn === null) return null;
  if (!signedIn)
    return (
      <SignIn
        onDone={() => {
          setSignedIn(true);
          router.refresh();
        }}
      />
    );

  return (
    <main className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-slate-100">Runs</h1>
        <nav className="flex gap-4 text-sm">
          {/* Hidden from a viewer because a link that answers 403 is not
              navigation. The refusal itself is the server's, not this. */}
          {me.data?.orgRole === "ORG_ADMIN" ? (
            <Link
              className="text-accent hover:underline"
              href="/settings/users"
            >
              People
            </Link>
          ) : null}
          <Link className="text-accent hover:underline" href="/projects">
            Projects →
          </Link>
        </nav>
      </div>
      <Card>
        <Runs />
      </Card>
    </main>
  );
}
