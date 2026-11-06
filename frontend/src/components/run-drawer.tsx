"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleX, Clock, LoaderCircle, MinusCircle, RotateCcw } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { api, ApiError, type RunDetail, type SourceFile, type Stage } from "@/lib/api";
import type { DictKey } from "@/lib/dict";
import { bytes, count, dateTime, seconds } from "@/lib/format";
import { useHealth } from "@/lib/hooks";
import { errorTitle, translate, useI18n, useLabel, type Lang } from "@/lib/i18n";
import { Badge, Button, Drawer, ErrorState, KeyValue, Loading, Notice, RUN_TONE, td, th } from "./ui";

const STAGE_ICON = {
  succeeded: <CircleCheck className="size-5 text-ok" aria-hidden />,
  failed: <CircleX className="size-5 text-bad" aria-hidden />,
  running: <LoaderCircle className="size-5 animate-spin text-accent" aria-hidden />,
  pending: <Clock className="size-5 text-faint" aria-hidden />,
  skipped: <MinusCircle className="size-5 text-faint" aria-hidden />,
} as Record<string, ReactNode>;

const FIXED_STAGE_MESSAGES: Record<string, DictKey> = {
  "New or changed rows landed in raw.source_record": "stagemsg.extract",
  "Warehouse quality gate passed": "stagemsg.gate",
  "Committed atomically with all warehouse changes": "stagemsg.publish",
  "Completed, then rolled back: nothing from this stage was published": "stagemsg.rolledBack",
  "Not run because an earlier stage failed": "stagemsg.skipped",
  "Not applicable to reprocess runs": "stagemsg.notApplicable",
};

/** Stage messages come from the backend in English; render them in the UI language. */
function stageMessage(s: Stage, run: RunDetail, lang: Lang): string | null {
  if (!s.message) return null;
  if (lang === "en") return s.message;
  if (FIXED_STAGE_MESSAGES[s.message]) return translate(lang, FIXED_STAGE_MESSAGES[s.message]);
  const m = s.message.match(/^(\d+) new deliveries: (\d+) landed, (\d+) duplicate, (\d+) conflict, (\d+) rejected$/);
  if (m) return translate(lang, "stagemsg.ingest", { deliveries: m[1], landed: m[2], duplicate: m[3], conflict: m[4], rejected: m[5] });
  if (s.status === "failed" && run.error) return translate(lang, `err.${run.error.code}.msg` as DictKey) ?? s.message;
  return s.message;
}

export function useDescribeParams() {
  const { t } = useI18n();
  return (p: Record<string, unknown>): string => {
    if (p.scope === "quarantine") return `${t("params.quarantine")}${Array.isArray(p.error_codes) && p.error_codes.length ? ` (${(p.error_codes as string[]).join(", ")})` : ""}`;
    if (p.scope === "run") return t("params.run", { id: String(p.source_run_id) });
    if (p.fail_at) return t("params.fail", { point: String(p.fail_at) });
    return "";
  };
}

export function fileMessage(f: SourceFile, lang: Lang): string | null {
  return lang === "ar" ? f.message_ar ?? f.message : f.message;
}

export function StageList({ run }: { run: RunDetail }) {
  const { t, lang } = useI18n();
  const label = useLabel();
  return (
    <ol className="space-y-1">
      {run.stages.map((s) => {
        const msg = stageMessage(s, run, lang);
        return (
          <li key={s.stage_seq} className="grid grid-cols-[1.5rem_1fr_auto] items-start gap-3 rounded-md px-2 py-2">
            <span title={label("status", s.status)}>{STAGE_ICON[s.status]}</span>
            <div className="min-w-0">
              <p className="text-sm font-medium">{label("stage", s.stage_name)}<span className="sr-only">{lang === "ar" ? "، " : ", "}{label("status", s.status)}</span></p>
              {msg && <p className={`text-xs ${s.status === "failed" ? "text-bad" : "text-muted"}`}>{msg}</p>}
              {(s.rows_read !== null || s.rows_written !== null) && (
                <p className="num text-xs text-muted">
                  {s.rows_read !== null && t("rd.read", { n: count(s.rows_read) })}
                  {s.rows_written !== null && t("rd.written", { n: count(s.rows_written) })}
                  {s.rows_rejected ? <span className="text-warn">{t("rd.rejected", { n: count(s.rows_rejected) })}</span> : null}
                </p>
              )}
            </div>
            <span className="num text-xs text-muted">{s.started_at_utc ? seconds(s.duration_seconds) : ""}</span>
          </li>
        );
      })}
    </ol>
  );
}

export function RunDrawer({ runId, onClose }: { runId: number | null; onClose: () => void }) {
  const { t, lang } = useI18n();
  const label = useLabel();
  const describe = useDescribeParams();
  const qc = useQueryClient();
  const health = useHealth();
  const q = useQuery({
    queryKey: ["run", runId], enabled: runId !== null,
    queryFn: () => api.get<RunDetail>(`/api/pipeline/runs/${runId}`),
    refetchInterval: (query) => (["queued", "running"].includes(query.state.data?.status ?? "") ? 1500 : false),
  });
  const retry = useMutation({
    mutationFn: () => api.post<{ run_id: number }>(`/api/pipeline/runs/${runId}/retry`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });
  const r = q.data;
  const extra = r ? describe(r.params) : "";
  const errMsg = r?.error ? (lang === "ar" ? `${t(`err.${r.error.code}.msg` as DictKey)}${r.error.code === "UNEXPECTED_ERROR" || r.error.code === "QUALITY_GATE_FAILED" ? ` ${r.error.message}` : ""}` : r.error.message) : "";
  const cols: DictKey[] = ["rd.col.read", "rd.col.new", "rd.col.unchanged", "rd.col.dup", "rd.col.accepted", "rd.col.rejected", "rd.col.inserted", "rd.col.updated"];
  return (
    <Drawer open={runId !== null} onClose={onClose} title={t("rd.title", { id: runId ?? "" })}
      description={r ? t("rd.desc", { mode: label("mode", r.mode), extra: extra ? (lang === "ar" ? `، ${extra}` : `, ${extra}`) : "", by: r.requested_by, time: dateTime(r.requested_at_utc) }) : undefined}>
      {q.isLoading ? <Loading /> : q.isError ? <ErrorState error={q.error} /> : r && (
        <div className="space-y-7">
          <div className="flex flex-wrap items-center gap-3">
            <Badge tone={RUN_TONE[r.status]}>{label("status", r.status)}</Badge>
            <span className="num text-sm text-muted">{r.started_at_utc ? t("rd.duration", { d: seconds(r.duration_seconds) }) : t("rd.waiting")}</span>
            {r.retry_of_run_id && <span className="text-sm text-muted">{t("rd.retryOf")} <Link className="text-accent underline" href={`/pipeline?run=${r.retry_of_run_id}`}>#{r.retry_of_run_id}</Link></span>}
            {r.retried_by.length > 0 && <span className="text-sm text-muted">{t("rd.retriedBy")} {r.retried_by.map((id) => <Link key={id} className="text-accent underline" href={`/pipeline?run=${id}`}>#{id}</Link>)}</span>}
          </div>

          {r.status === "queued" && <Notice>{t("rd.queued")}</Notice>}

          {r.error && (
            <div role="alert" className="rounded-lg border border-bad/30 bg-bad-soft p-4 text-sm">
              <p className="font-medium text-bad">{errorTitleForRun(r.error.code, lang)}{r.error.stage ? t("rd.during", { stage: label("stage", r.error.stage) }) : ""}</p>
              <p className="mt-1 text-ink">{errMsg}</p>
              <p className="mt-2 text-muted">{t("rd.nothing")}</p>
              {r.retried_by.length === 0 && (
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <Button variant="primary" size="sm" onClick={() => retry.mutate()} busy={retry.isPending} disabled={health.data?.read_only}>
                    <RotateCcw className="size-4" aria-hidden /> {t("rd.retry")}
                  </Button>
                  <span className="text-xs text-muted">{t("rd.retryScope", { mode: label("mode", r.mode), noFail: r.params.fail_at ? t("rd.retryNoFail") : "" })}</span>
                </div>
              )}
              {retry.isError && <p className="mt-2 text-bad">{(retry.error as ApiError).message}</p>}
              {retry.isSuccess && <p className="mt-2 text-ok">{t("rd.retryQueued", { id: retry.data.run_id })}</p>}
            </div>
          )}

          <section aria-label={t("rd.stages")}><h3 className="mb-2 font-semibold">{t("rd.stages")}</h3><StageList run={r} /></section>

          {r.entity_stats.length > 0 && (
            <section aria-label={t("rd.rows")}>
              <h3 className="mb-2 font-semibold">{t("rd.rows")}</h3>
              <div className="overflow-x-auto rounded-lg border border-line bg-surface">
                <table className="num w-full min-w-[640px] text-end">
                  <thead className="border-b border-line"><tr>
                    <th className={`${th} text-start`}>{t("rd.col.entity")}</th>
                    {cols.map((h) => <th key={h} className={`${th} text-end`}>{t(h)}</th>)}
                  </tr></thead>
                  <tbody className="divide-y divide-line">
                    {r.entity_stats.map((e) => (
                      <tr key={e.entity}>
                        <td className={`${td} text-start`}>{label("entity", e.entity)}</td>
                        {[e.rows_read, e.rows_new_version, e.rows_unchanged, e.rows_duplicate, e.rows_accepted].map((v, i) => <td key={i} className={td}>{count(v)}</td>)}
                        <td className={td}>{e.rows_rejected ? <Link className="text-warn underline" href={`/quality?run=${r.run_id}&entity=${e.entity}`}>{count(e.rows_rejected)}</Link> : 0}</td>
                        <td className={td}>{count(e.rows_inserted)}</td><td className={td}>{count(e.rows_updated)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-xs text-muted">{t("rd.rowsNote")}</p>
            </section>
          )}

          {r.files.length > 0 && (
            <section aria-label={t("rd.files")}>
              <h3 className="mb-2 font-semibold">{t("rd.files")}</h3>
              <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
                {r.files.map((f) => (
                  <li key={f.file_id} className="px-4 py-2.5 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium" dir="ltr">{f.file_name}</span>
                      <Badge tone={f.status === "processed" ? "ok" : f.status === "landed" ? "info" : f.status === "duplicate" ? "neutral" : "bad"}>{label("status", f.status)}</Badge>
                    </div>
                    <p className="num text-xs text-muted">{f.record_count !== null ? t("rd.records", { n: count(f.record_count) }) : ""}<span dir="ltr">{bytes(f.file_size_bytes)}, SHA-256 {f.file_checksum.slice(0, 12)}…</span></p>
                    {fileMessage(f, lang) && <p className="text-xs text-muted">{fileMessage(f, lang)}</p>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section aria-label={t("rd.execution")}>
            <h3 className="mb-2 font-semibold">{t("rd.execution")}</h3>
            <KeyValue items={[
              [t("rd.worker"), r.worker_id ? <span dir="ltr">{r.worker_id}</span> : t("rd.notClaimed")],
              [t("rd.started"), dateTime(r.started_at_utc)], [t("rd.finished"), dateTime(r.finished_at_utc)],
              [t("rd.heartbeat"), dateTime(r.heartbeat_at_utc)],
              [t("rd.quarantined"), count(r.quarantined_in_run)],
            ]} />
          </section>
        </div>
      )}
    </Drawer>
  );
}

function errorTitleForRun(code: string, lang: Lang): string {
  const key = `err.${code}` as DictKey;
  const s = translate(lang, key);
  return s === key ? errorTitle(code, lang) : s;
}
