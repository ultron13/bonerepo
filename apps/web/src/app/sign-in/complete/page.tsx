"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { completeSignIn } from "@/lib/api";

/**
 * Where an identity provider hands somebody back.
 *
 * The URL carries nothing: the callback set an httpOnly refresh cookie, and
 * this trades it for an access token over a request nobody else sees. An
 * access token in the fragment would have worked too, and would have survived
 * in browser history and in anything that logs a location for as long as it
 * stayed valid.
 */
export default function CompleteSignIn() {
  const router = useRouter();
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    completeSignIn()
      .then(() => router.replace("/"))
      .catch((cause) =>
        setProblem(
          cause instanceof Error
            ? cause.message
            : "The sign-in could not be completed.",
        ),
      );
  }, [router]);

  return (
    <main className="mx-auto mt-24 max-w-sm space-y-4 text-center">
      <h1 className="text-xl font-semibold text-slate-100">Plimsoll</h1>
      {problem ? (
        <>
          <p className="text-sm text-fail">{problem}</p>
          <a className="text-sm text-accent hover:underline" href="/">
            Back to sign in
          </a>
        </>
      ) : (
        <p className="text-sm text-muted">Signing you in…</p>
      )}
    </main>
  );
}
