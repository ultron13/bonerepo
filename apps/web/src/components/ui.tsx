import type { ReactNode } from "react";

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-surface">
      {title ? (
        <h2 className="border-b border-line px-4 py-3 text-sm font-semibold tracking-wide text-slate-300">
          {title}
        </h2>
      ) : null}
      <div className="p-4">{children}</div>
    </section>
  );
}

const VERDICT_STYLE: Record<string, string> = {
  PASS: "border-pass/40 bg-pass/10 text-pass",
  COMPLETED: "border-pass/40 bg-pass/10 text-pass",
  WARNING: "border-warn/40 bg-warn/10 text-warn",
  SKIPPED: "border-line bg-line/40 text-muted",
  FAIL: "border-fail/40 bg-fail/10 text-fail",
  FAILED: "border-fail/40 bg-fail/10 text-fail",
  CANCELLED: "border-line bg-line/40 text-muted",
  RUNNING: "border-accent/40 bg-accent/10 text-accent",
};

/**
 * Status never travels by colour alone: the label is always present, so the
 * meaning survives a monochrome screen, a printed report, and anyone who does
 * not distinguish red from green.
 */
export function Status({ value }: { value: string }) {
  const style = VERDICT_STYLE[value] ?? "border-line bg-line/40 text-slate-300";
  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 font-mono text-xs ${style}`}
    >
      {value}
    </span>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-0.5 font-mono text-sm text-slate-200">{children}</div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-muted">{children}</p>;
}
