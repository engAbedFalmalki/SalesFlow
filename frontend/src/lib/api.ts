// Thin client for the SalesFlow FastAPI backend. Money arrives as decimal strings.

// "/" means same origin: the public build (scripts/public-link.ps1) proxies /api to the backend.
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

// ---- static demo (Netlify): no server; data is read from /static-data and computed in the browser.
export const STATIC_DATA = process.env.NEXT_PUBLIC_STATIC_DATA === "1";
const EXPORT_LIMIT = 50_000; // same as the API's max_export_rows

let staticApi: Promise<import("./static-api").StaticApi> | null = null;
function loadStatic() {
  staticApi ??= (async () => {
    const load = (name: string) => fetch(`/static-data/${name}.json`).then((r) => {
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    });
    const [{ createStaticApi }, snap, data] = await Promise.all([import("./static-api"), load("snapshots"), load("data")]);
    return createStaticApi(snap, data);
  })().catch((e) => {
    staticApi = null; // let a retry try again
    throw new ApiError(0, "NETWORK", `Could not load the demo data (${e instanceof Error ? e.message : e}).`);
  });
  return staticApi;
}

async function staticGet<T>(path: string, params: Params): Promise<T> {
  const engine = await loadStatic();
  try {
    return engine.get(path, params) as T;
  } catch (e) {
    const err = e as { status?: number; code?: string; message?: string };
    throw new ApiError(err.status ?? 500, err.code ?? "ERROR", err.message ?? "Unexpected error.");
  }
}

const readOnly = <T>(): Promise<T> =>
  Promise.reject(new ApiError(403, "READ_ONLY", "This demo is read-only; pipeline and review actions are disabled."));

export const api = {
  get: <T>(path: string, params: Params = {}) => (STATIC_DATA ? staticGet<T>(path, params) : request<T>(path + qs(params))),
  post: <T>(path: string, body: unknown) => (STATIC_DATA ? readOnly<T>() : request<T>(path, { method: "POST", body: JSON.stringify(body) })),
  patch: <T>(path: string, body: unknown) => (STATIC_DATA ? readOnly<T>() : request<T>(path, { method: "PATCH", body: JSON.stringify(body) })),
  url: (path: string, params: Params = {}) => `${API_BASE}${path}${qs(params)}`,
};

/** Static demo only: builds the orders workbook in the browser and downloads it. */
export async function downloadStaticOrdersXlsx(f: import("./static-export").ExportFilters, lang: "en" | "ar", filters?: import("./hooks").Filters) {
  const engine = await loadStatic();
  const rows = engine.exportRows(f, EXPORT_LIMIT);
  const { buildOrdersWorkbook } = await import("./static-export");
  const wb = await buildOrdersWorkbook(rows, f, lang, filters, EXPORT_LIMIT);
  const blob = new Blob([await wb.xlsx.writeBuffer()], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `salesflow_orders_${f.date_from}_${f.date_to}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}

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
