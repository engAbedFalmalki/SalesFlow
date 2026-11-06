// Thin client for the SalesFlow FastAPI backend. Money arrives as decimal strings.

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

type Params = Record<string, string | number | boolean | string[] | null | undefined>;

export function qs(params: Params): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x));
    else sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  } catch {
    throw new ApiError(0, "NETWORK", `Cannot reach the SalesFlow API at ${API_BASE}. Start it with: python -m salesflow api`);
  }
  if (!res.ok) {
    let body: { error?: { code?: string; message?: string; details?: unknown } } = {};
    try { body = await res.json(); } catch { /* not JSON */ }
    throw new ApiError(res.status, body.error?.code ?? "HTTP_ERROR", body.error?.message ?? `Request failed (${res.status})`, body.error?.details);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string, params: Params = {}) => request<T>(path + qs(params)),
  post: <T>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  url: (path: string, params: Params = {}) => `${API_BASE}${path}${qs(params)}`,
};

// ---- response shapes (only the fields the UI uses)
export type Metric = { value: string | number | null; previous: string | number | null; change: string | number | null; change_pct: string | null };
export type Coverage = { min_order_date: string | null; max_order_date: string | null; min_date: string | null; max_date: string | null; max_transaction_date: string | null };
export type Overview = {
  period: { date_from: string; date_to: string; days: number };
  comparison: { date_from: string; date_to: string; days: number; available: boolean; reason: string | null };
  metrics: Record<string, Metric>;
  coverage: Coverage;
};
export type SeriesPoint = { bucket: string; active_ordered_value: string; orders: number; successful_collections: string; successful_refunds: string; net_collections: string };
export type RunSummary = {
  run_id: number; mode: string; status: "queued" | "running" | "succeeded" | "failed"; requested_by: string;
  params: Record<string, unknown>; retry_of_run_id: number | null; requested_at_utc: string; started_at_utc: string | null;
  finished_at_utc: string | null; heartbeat_at_utc: string | null; worker_id: string | null; current_stage: string | null;
  duration_seconds: string | null; stages_succeeded: number; stages_total: number;
  error: { stage: string | null; code: string; message: string } | null;
};
export type Stage = { stage_seq: number; stage_name: string; label: string; status: string; started_at_utc: string | null; finished_at_utc: string | null; duration_seconds: string | null; rows_read: number | null; rows_written: number | null; rows_rejected: number | null; message: string | null; details: unknown };
export type EntityStats = { entity: string; rows_read: number; rows_new_version: number; rows_unchanged: number; rows_duplicate: number; rows_evaluated: number; rows_accepted: number; rows_rejected: number; rows_inserted: number; rows_updated: number; rows_dw_unchanged: number };
export type SourceFile = { file_id: number; source_system: string; file_name: string; status: string; record_count: number | null; file_size_bytes: number; file_checksum: string; duplicate_of_file_id: number | null; message: string | null; message_ar: string | null; received_at_utc: string };
export type RunDetail = RunSummary & { stages: Stage[]; entity_stats: EntityStats[]; files: SourceFile[]; summary: { duration_seconds: number; warehouse_totals: Record<string, string | number> } | null; retried_by: number[]; quarantined_in_run: number };
export type Freshness = {
  last_successful_run: { run_id: number; mode: string; finished_at_utc: string; minutes_ago: number } | null;
  last_run: { run_id: number; mode: string; status: string; error_code: string | null } | null;
  active_run: { run_id: number; status: string; current_stage: string | null } | null;
  watermarks: { source_name: string; watermark_utc: string; last_run_id: number }[];
  files: { source_system: string; last_received_at_utc: string; processed_files: number; problem_files: number; latest_processed_file: string | null }[];
  open_quarantine_records: number;
  coverage: Coverage;
};
export type Health = { status: string; env: string; read_only: boolean; requires_admin_token: boolean };
