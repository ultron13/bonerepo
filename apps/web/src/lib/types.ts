/**
 * The shapes this interface reads.
 *
 * Hand-written rather than imported from the generated OpenAPI types, and
 * deliberately narrow: these are the fields the pages actually use, so a change
 * to one of them breaks a build rather than a page at run time. The generated
 * types in `@plimsoll/contracts` remain the contract of record.
 */

export type RunStatus =
  | "QUEUED"
  | "ALLOCATING"
  | "STARTING"
  | "RUNNING"
  | "STOPPING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export const TERMINAL: ReadonlySet<RunStatus> = new Set<RunStatus>([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
]);

export interface Page<T> {
  items: T[];
  nextCursor?: string | null;
}

export interface Project {
  id: string;
  name: string;
  projectKey: string;
}

export interface PerformanceTest {
  id: string;
  name: string;
}

export interface GeneratorView {
  ordinal: number;
  status: string;
  assignedUsers: number;
  lastHeartbeat: string | null;
}

export interface RunStatusResponse {
  id: string;
  status: RunStatus;
  degraded: boolean;
  startedAt: string | null;
  endedAt: string | null;
  generators: GeneratorView[];
}

export interface SlaRuleOutcome {
  name: string;
  metric: string;
  entity: string | null;
  operator: string;
  threshold: number;
  actual: number | null;
  verdict: "PASS" | "WARNING" | "FAIL" | "SKIPPED";
  detail: string;
}

export interface Run {
  id: string;
  projectId: string;
  runNumber: number;
  status: RunStatus;
  degraded: boolean;
  startedAt: string | null;
  endedAt: string | null;
  createdAt: string;
  slaResult: string | null;
  summary: { generators?: number; sla?: { detail: string; rules: SlaRuleOutcome[] } } | null;
  configurationSnapshot: { bundleSha256?: string; plans?: { commitSha: string }[] };
}

export interface TransactionSummary {
  transaction: string;
  count: number;
  errorCount: number;
  errorRate: number;
  min: number;
  max: number;
  mean: number;
  p50: number;
  p90: number;
  p95: number;
  p99: number;
  throughput: number;
}

export interface RunMetrics {
  runId: string;
  totalSamples: number;
  totalErrors: number;
  transactions: TransactionSummary[];
}

export interface ErrorGroup {
  fingerprint: string;
  errorCode: string | null;
  message: string | null;
  transaction: string | null;
  count: number;
  firstSeen: string;
  lastSeen: string;
  sample: string | null;
}

export interface RunErrors {
  runId: string;
  total: number;
  items: ErrorGroup[];
}

export interface Artifact {
  key: string;
  name: string;
  size: number;
  lastModified: string;
}

/** A live window, as the socket publishes it. Every field arrives as text. */
export interface MetricEvent {
  type: "metric";
  runId: string;
  transaction: string;
  windowStart: string;
  count: string;
  errorCount: string;
  p50: string;
  p95: string;
  p99: string;
}

export interface StatusEvent {
  type: "run.status";
  runId: string;
  status: RunStatus;
}

export type LiveEvent = MetricEvent | StatusEvent;

export interface ScriptRepo {
  id: string;
  name: string;
  repoUrl: string;
  planPath: string;
  defaultRef: string;
  status: string;
}

export interface GeneratorPool {
  id: string;
  name: string;
  runtime: string;
  maxGenerators: number;
  maxVusPerGenerator: number;
  capacity: number;
}

export interface VerifyFinding {
  code: string;
  severity: string;
  message: string;
}

export interface VerifyReport {
  ok: boolean;
  findings: VerifyFinding[];
}
