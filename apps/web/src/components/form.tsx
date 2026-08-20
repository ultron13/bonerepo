"use client";

import { useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api";

export function Input({
  label,
  value,
  onChange,
  placeholder,
  hint,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
  type?: string;
}) {
  return (
    <label className="block text-sm text-muted">
      {label}
      <input
        className="mt-1 w-full rounded border border-line bg-ink px-3 py-2 font-mono text-sm text-slate-200"
        value={value}
        type={type}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint ? <span className="mt-1 block text-xs text-muted">{hint}</span> : null}
    </label>
  );
}

export function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="block text-sm text-muted">
      {label}
      <select
        className="mt-1 w-full rounded border border-line bg-ink px-3 py-2 font-mono text-sm text-slate-200"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * A form that reports what the API said.
 *
 * The API answers a refusal with a code and a message chosen to be actionable
 * -- every failing check at once, rather than the first one. Replacing that
 * with "something went wrong" would throw away the part worth reading.
 */
export function Form({
  submit,
  onSubmit,
  children,
}: {
  submit: string;
  onSubmit: () => Promise<void>;
  children: ReactNode;
}) {
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  return (
    <form
      className="space-y-4"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        setError(null);
        setDetail([]);
        try {
          await onSubmit();
        } catch (cause) {
          if (cause instanceof ApiError) {
            setError(cause.message);
            setDetail(cause.checks());
          } else {
            setError(cause instanceof Error ? cause.message : "That did not work.");
          }
        } finally {
          setBusy(false);
        }
      }}
    >
      {children}
      {error ? (
        <div className="rounded border border-fail/40 bg-fail/10 p-3 text-sm text-fail">
          <p>{error}</p>
          {detail.length > 0 ? (
            <ul className="mt-2 space-y-1 font-mono text-xs">
              {detail.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      <button
        className="rounded bg-accent px-3 py-2 text-sm font-medium text-ink disabled:opacity-50"
        disabled={busy}
      >
        {busy ? "Working…" : submit}
      </button>
    </form>
  );
}
