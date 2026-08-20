"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Form, Input, Select } from "@/components/form";
import { Card, Empty, Status } from "@/components/ui";
import { api } from "@/lib/api";
import type { OrgRole, Page as Paged, User, UserInvited } from "@/lib/types";

const ROLES: { value: OrgRole; label: string }[] = [
  { value: "VIEWER", label: "Viewer — can read projects, scripts and results" },
  {
    value: "ORG_ADMIN",
    label: "Administrator — can also change settings and people",
  },
];

function Invited({
  user,
  onDismiss,
}: {
  user: UserInvited;
  onDismiss: () => void;
}) {
  return (
    <div className="mb-6 rounded border border-accent/40 bg-accent/10 p-3">
      <p className="text-sm text-slate-200">
        {user.name} has been added. This password is shown once — pass it on
        now.
      </p>
      <p className="mt-2 select-all font-mono text-sm text-accent">
        {user.temporaryPassword}
      </p>
      <button
        className="mt-2 text-xs text-muted hover:text-slate-300"
        onClick={onDismiss}
      >
        Dismiss
      </button>
    </div>
  );
}

export default function UsersPage() {
  const queries = useQueryClient();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [orgRole, setOrgRole] = useState<string>("VIEWER");
  const [invited, setInvited] = useState<UserInvited | null>(null);

  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => api.get<Paged<User>>("/api/v1/users"),
  });

  const refresh = () => queries.invalidateQueries({ queryKey: ["users"] });

  const invite = useMutation({
    mutationFn: () =>
      api.post<UserInvited>("/api/v1/users", { email, name, orgRole }),
    onSuccess: (user) => {
      setInvited(user);
      setEmail("");
      setName("");
      refresh();
    },
  });

  // Deactivation and role changes are refused when they would leave the
  // organisation without an administrator, so the API's message is what gets
  // shown rather than a rule guessed at again here.
  const change = useMutation({
    mutationFn: ({ id, role }: { id: string; role: OrgRole }) =>
      api.patch<User>(`/api/v1/users/${id}`, { orgRole: role }),
    onSuccess: refresh,
  });

  const setActive = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api.post<User>(
        `/api/v1/users/${id}/${active ? "reactivate" : "deactivate"}`,
      ),
    onSuccess: refresh,
  });

  const problem = [invite.error, change.error, setActive.error].find(
    Boolean,
  ) as Error | undefined;

  return (
    <main className="space-y-6">
      <div>
        <Link className="text-sm text-accent hover:underline" href="/">
          ← Runs
        </Link>
        <h1 className="mt-1 text-xl font-semibold text-slate-100">People</h1>
      </div>

      <Card title="Organisation members">
        {invited ? (
          <Invited user={invited} onDismiss={() => setInvited(null)} />
        ) : null}
        {problem ? (
          <p className="mb-6 rounded border border-fail/40 bg-fail/10 p-3 text-sm text-fail">
            {problem.message}
          </p>
        ) : null}

        {users.data && users.data.items.length > 0 ? (
          <ul className="mb-6 divide-y divide-line">
            {users.data.items.map((user) => (
              <li
                key={user.id}
                className="flex items-center justify-between gap-4 py-3"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-slate-200">{user.name}</p>
                  <p className="truncate font-mono text-xs text-muted">
                    {user.email}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <Status value={user.status} />
                  <select
                    className="rounded border border-line bg-ink px-2 py-1 text-xs text-slate-300"
                    value={user.orgRole}
                    disabled={change.isPending}
                    onChange={(event) =>
                      change.mutate({
                        id: user.id,
                        role: event.target.value as OrgRole,
                      })
                    }
                  >
                    {ROLES.map((role) => (
                      <option key={role.value} value={role.value}>
                        {role.value === "ORG_ADMIN"
                          ? "Administrator"
                          : "Viewer"}
                      </option>
                    ))}
                  </select>
                  <button
                    className="rounded border border-line px-2 py-1 text-xs text-slate-300 hover:border-accent disabled:opacity-50"
                    disabled={setActive.isPending}
                    onClick={() =>
                      setActive.mutate({
                        id: user.id,
                        active: user.status !== "ACTIVE",
                      })
                    }
                  >
                    {user.status === "ACTIVE" ? "Deactivate" : "Reactivate"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>
            {users.isError ? "Only an administrator can see this." : "Loading…"}
          </Empty>
        )}

        <Form
          submit="Add person"
          onSubmit={() => invite.mutateAsync().then(() => undefined)}
        >
          <Input
            label="Email"
            value={email}
            onChange={setEmail}
            placeholder="name@example.com"
          />
          <Input
            label="Name"
            value={name}
            onChange={setName}
            placeholder="Sam Patel"
          />
          <Select
            label="Role"
            value={orgRole}
            onChange={setOrgRole}
            options={ROLES.map((role) => ({
              value: role.value,
              label: role.label,
            }))}
          />
        </Form>
      </Card>
    </main>
  );
}
