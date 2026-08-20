"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Form, Input } from "@/components/form";
import { Card, Empty } from "@/components/ui";
import { api } from "@/lib/api";
import type { Page as Paged, Project } from "@/lib/types";

function NewProject() {
  const queries = useQueryClient();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.post<Project>("/api/v1/projects", { name, projectKey: key.toUpperCase() }),
    onSuccess: () => {
      setName("");
      setKey("");
      queries.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  return (
    <Card title="New project">
      <Form submit="Create project" onSubmit={() => create.mutateAsync().then(() => undefined)}>
        <Input label="Name" value={name} onChange={setName} placeholder="Checkout service" />
        <Input
          label="Project key"
          value={key}
          onChange={setKey}
          placeholder="CHECKOUT"
          hint="Short and stable. It prefixes the run numbers and cannot be changed."
        />
      </Form>
    </Card>
  );
}

export default function ProjectsPage() {
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Paged<Project>>("/api/v1/projects?limit=100"),
  });

  return (
    <main className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-slate-100">Projects</h1>
        <Link className="text-sm text-accent hover:underline" href="/">
          Runs →
        </Link>
      </div>

      <Card>
        {projects.data && projects.data.items.length > 0 ? (
          <ul className="divide-y divide-line">
            {projects.data.items.map((project) => (
              <li key={project.id} className="py-2 first:pt-0 last:pb-0">
                <Link
                  className="font-mono text-sm text-accent hover:underline"
                  href={`/projects/${project.id}`}
                >
                  {project.projectKey}
                </Link>
                <span className="ml-3 text-sm text-slate-300">{project.name}</span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No projects yet. Create one below.</Empty>
        )}
      </Card>

      <NewProject />
    </main>
  );
}
