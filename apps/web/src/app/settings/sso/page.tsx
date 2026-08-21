"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Form, Input } from "@/components/form";
import { Card, Empty } from "@/components/ui";
import { ApiError, api } from "@/lib/api";

interface Provider {
  id: string;
  issuer: string;
  clientId: string;
  groupsClaim: string;
  adminGroup: string | null;
  allowedDomains: string[];
  enabled: boolean;
  startUrl: string;
}

export default function SsoPage() {
  const queries = useQueryClient();
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [groupsClaim, setGroupsClaim] = useState("groups");
  const [adminGroup, setAdminGroup] = useState("");
  const [domains, setDomains] = useState("");

  const provider = useQuery({
    queryKey: ["identity-provider"],
    // A 404 is the answer "none is configured", not a failure. Letting it
    // throw would leave the last successful value in place -- so turning
    // single sign-on off would still show it as on until the page reloaded.
    queryFn: () =>
      api
        .get<Provider>("/api/v1/identity-provider")
        .catch((cause) =>
          cause instanceof ApiError && cause.status === 404
            ? null
            : Promise.reject(cause),
        ),
    retry: false,
  });

  const configure = useMutation({
    mutationFn: () =>
      api.put<Provider>("/api/v1/identity-provider", {
        issuer,
        clientId,
        clientSecret,
        groupsClaim,
        adminGroup: adminGroup || null,
        allowedDomains: domains
          .split(",")
          .map((entry) => entry.trim())
          .filter(Boolean),
      }),
    onSuccess: () => {
      setClientSecret("");
      queries.invalidateQueries({ queryKey: ["identity-provider"] });
    },
  });

  const remove = useMutation({
    mutationFn: () => api.delete("/api/v1/identity-provider"),
    onSuccess: () =>
      queries.invalidateQueries({ queryKey: ["identity-provider"] }),
  });

  const problem = (configure.error ?? remove.error) as Error | undefined;
  const current = provider.data;

  return (
    <main className="space-y-6">
      <div>
        <Link className="text-sm text-accent hover:underline" href="/">
          ← Runs
        </Link>
        <h1 className="mt-1 text-xl font-semibold text-slate-100">
          Single sign-on
        </h1>
      </div>

      <Card title="Identity provider">
        {problem ? (
          <p className="mb-6 rounded border border-fail/40 bg-fail/10 p-3 text-sm text-fail">
            {problem.message}
          </p>
        ) : null}

        {current ? (
          <div className="mb-6 space-y-2 rounded border border-line p-3 text-sm">
            <p className="text-slate-200">{current.issuer}</p>
            <p className="font-mono text-xs text-muted">
              client {current.clientId}
            </p>
            <p className="text-xs text-muted">
              Accounts are created for {current.allowedDomains.join(", ")}
              {current.adminGroup
                ? `; members of ${current.adminGroup} administer`
                : ""}
            </p>
            <div>
              <p className="text-xs text-muted">
                Share this link with your organisation:
              </p>
              <p className="select-all font-mono text-xs text-accent">
                {current.startUrl}
              </p>
            </div>
            <button
              className="rounded border border-line px-2 py-1 text-xs text-slate-300 hover:border-fail"
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
            >
              Turn off
            </button>
          </div>
        ) : (
          <Empty>
            {provider.isLoading
              ? "Loading…"
              : "No provider is configured. Passwords only."}
          </Empty>
        )}

        <Form
          submit={current ? "Replace configuration" : "Turn on single sign-on"}
          onSubmit={() => configure.mutateAsync().then(() => undefined)}
        >
          <Input
            label="Issuer"
            value={issuer}
            onChange={setIssuer}
            placeholder="https://login.example.com"
            hint="Just the issuer. Everything below it is read from the provider's own discovery document, and checked before this is saved."
          />
          <Input label="Client ID" value={clientId} onChange={setClientId} />
          <Input
            label="Client secret"
            value={clientSecret}
            onChange={setClientSecret}
            type="password"
            hint="Stored encrypted, and never shown again."
          />
          <Input
            label="Allowed email domains"
            value={domains}
            onChange={setDomains}
            placeholder="example.com, example.co.uk"
            hint="Required. An address outside these gets no account, however valid its token."
          />
          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Groups claim"
              value={groupsClaim}
              onChange={setGroupsClaim}
            />
            <Input
              label="Administrator group"
              value={adminGroup}
              onChange={setAdminGroup}
              placeholder="plimsoll-admins"
              hint="Membership grants and withdraws administration on each sign-in."
            />
          </div>
        </Form>
      </Card>
    </main>
  );
}
