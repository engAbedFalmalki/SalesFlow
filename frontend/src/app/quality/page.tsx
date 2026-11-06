"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { RotateCcw } from "lucide-react";
import Link from "next/link";
import { Suspense, useRef, useState } from "react";
import { Figure, SpotlightPanel, useBarReveal } from "@/components/motion";
import { Badge, Button, ConfirmDialog, Drawer, Empty, ErrorState, Field, inputClass, KeyValue, Loading, Notice, PageHeader, Pagination, Panel, Section, Segmented, Select, td, th, type Tone } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { count, dateTime } from "@/lib/format";
import { useHealth, useRunWatch, useUrlState } from "@/lib/hooks";
import { checkName, errorTitle, useI18n, useLabel } from "@/lib/i18n";

type Check = { check_code: string; check_name: string; check_level: string; entity: string; severity: string; status: string; checked_count: number; failed_count: number; details: string | null };
type Summary = { run_id: number | null; checks: Check[]; checks_passed: number; checks_failed: number; quarantine_by_code: { error_code: string; entity: string; status: string; records: number }[]; quarantine_by_status: { status: string; records: number }[] };
type QRow = { quarantine_id: number; entity: string; source_system: string; source_record_id: string; source_file_name: string | null; run_id: number; error_code: string; error_reason: string; error_reason_ar: string | null; status: string; evaluation_count: number; created_at_utc: string };
type QDetail = QRow & { errors: { code: string; field: string | null; message: string; message_ar?: string }[]; payload: string; raw_id: number | null; last_evaluated_run_id: number; resolved_run_id: number | null; status_note: string | null; history: { quarantine_id: number; raw_id: number; run_id: number; error_code: string; status: string; created_at_utc: string }[] };

const Q_TONE: Record<string, Tone> = { open: "warn", resolved: "ok", superseded: "neutral", ignored: "info" };
const ENTITIES = ["customer", "product", "order", "order_item", "payment_txn", "file"];
const REPROCESSABLE = new Set(["MISSING_PRODUCT_REF", "MISSING_CUSTOMER_REF", "MISSING_ORDER_REF", "INVALID_REFUND_REF"]);

function pretty(payload: string): string {
  try { return JSON.stringify(JSON.parse(payload), null, 2); } catch { return payload; }
}

function QuarantineDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const { t, lang } = useI18n();
  const label = useLabel();
  const qc = useQueryClient();
  const health = useHealth();
  const [note, setNote] = useState("");
  const q = useQuery({ queryKey: ["qrec", id], enabled: id !== null, queryFn: () => api.get<QDetail>(`/api/quality/quarantine/${id}`) });
  const review = useMutation({
    mutationFn: (status: "ignored" | "open") => api.patch<QDetail>(`/api/quality/quarantine/${id}`, { status, note: note || undefined }),
    onSuccess: (d) => { qc.setQueryData(["qrec", id], d); qc.invalidateQueries({ queryKey: ["quarantine"] }); qc.invalidateQueries({ queryKey: ["quality"] }); setNote(""); },
  });
  const r = q.data;
  return (
    <Drawer open={id !== null} onClose={onClose} title={r ? errorTitle(r.error_code, lang) : t("qd.default")}
      description={r ? t("qd.from", { entity: label("entity", r.entity), id: r.source_record_id, source: r.source_file_name ?? r.source_system }) : undefined}>
      {q.isLoading ? <Loading /> : q.isError ? <ErrorState error={q.error} /> : r && (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={Q_TONE[r.status]}>{label("status", r.status)}</Badge>
            <span className="text-sm text-muted">{t("qd.by")} <Link className="text-accent underline" href={`/pipeline?run=${r.run_id}`}>#{r.run_id}</Link>{lang === "ar" ? "، " : ", "}{t("qd.evaluated", { n: r.evaluation_count })}</span>
          </div>
          <section aria-label={t("qd.why")}>
            <h3 className="mb-2 font-semibold">{t("qd.why")}</h3>
            <ul className="space-y-2">
              {r.errors.map((e, i) => (
                <li key={i} className="rounded-md border border-line bg-surface px-3 py-2 text-sm">
                  <p className="font-medium">{errorTitle(e.code, lang)}{e.field ? <span className="font-normal text-muted">{t("qd.inField", { field: e.field })}</span> : null}</p>
                  <p className="text-muted">{lang === "ar" && e.message_ar ? e.message_ar : e.message}</p>
                </li>
              ))}
            </ul>
          </section>
          <section aria-label={t("qd.payload")}>
            <h3 className="mb-2 font-semibold">{t("qd.payload")}</h3>
            <pre dir="ltr" className="max-h-80 overflow-auto rounded-md border border-line bg-surface p-3 text-start text-xs leading-relaxed" data-lenis-prevent>{pretty(r.payload)}</pre>
          </section>
          <KeyValue items={[[t("qd.sourceSystem"), r.source_system], [t("qd.rawId"), r.raw_id ?? t("qd.fileLevel")], [t("qd.first"), dateTime(r.created_at_utc)],
            [t("qd.lastEval"), `#${r.last_evaluated_run_id}`], [t("qd.resolvedBy"), r.resolved_run_id ? `#${r.resolved_run_id}` : "–"], [t("qd.note"), r.status_note ?? "–"]]} />
          {r.history.length > 1 && (
            <section aria-label={t("qd.versions")}>
              <h3 className="mb-2 font-semibold">{t("qd.versions")}</h3>
              <ul className="text-sm">{r.history.map((h) => <li key={h.quarantine_id} className="num text-muted">#{h.quarantine_id}: raw {h.raw_id}, #{h.run_id}, {errorTitle(h.error_code, lang)}, {label("status", h.status)}</li>)}</ul>
            </section>
          )}
          {(r.status === "open" || r.status === "ignored") && r.entity !== "file" && (
            <section aria-label={t("qd.review")} className="rounded-lg border border-line bg-surface p-4">
              <h3 className="font-semibold">{t("qd.review")}</h3>
              <p className="mt-1 text-sm text-muted">{r.status === "open" ? t("qd.reviewOpen") : t("qd.reviewIgnored")}</p>
              <Field label={t("qd.noteLabel")} htmlFor="qnote"><input id="qnote" className={clsx(inputClass, "mt-1")} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
              <div className="mt-3">
                {r.status === "open"
                  ? <Button onClick={() => review.mutate("ignored")} busy={review.isPending} disabled={health.data?.read_only}>{t("qd.ignore")}</Button>
                  : <Button onClick={() => review.mutate("open")} busy={review.isPending} disabled={health.data?.read_only}>{t("qd.reopen")}</Button>}
              </div>
              {review.isError && <p className="mt-2 text-sm text-bad">{(review.error as ApiError).message}</p>}
            </section>
          )}
          {r.entity === "file" && <Notice tone="info">{t("qd.fileRejected")}</Notice>}
        </div>
      )}
    </Drawer>
  );
}

/** Tells the user what a reprocess run actually changed, once it has finished. */
function ReprocessOutcome({ runId }: { runId: number }) {
  const { t } = useI18n();
  const run = useRunWatch(runId);
  const r = run.data;
  const link = <Link className="ms-1 underline" href={`/pipeline?run=${runId}`}>{t("q.viewRun")}</Link>;
  if (!r || r.status === "queued" || r.status === "running") return <Notice tone="info">{t("q.running", { id: runId })}</Notice>;
  if (r.status === "failed") return <Notice tone="bad">{t("q.runFailed", { id: runId, msg: r.error?.message ?? "" })}{link}</Notice>;
  const st = r.stages.find((x) => x.stage_name === "stage_validate");
  const checked = st?.rows_read ?? 0, passed = st?.rows_written ?? 0, left = st?.rows_rejected ?? 0;
  const [tone, msg] = checked === 0 ? ["info", t("q.doneEmpty", { id: runId })]
    : passed === 0 ? ["warn", t("q.doneNone", { id: runId, n: count(checked) })]
    : left === 0 ? ["ok", t("q.doneAll", { id: runId, ok: count(passed) })]
    : ["ok", t("q.doneSome", { id: runId, ok: count(passed), left: count(left) })];
  return <Notice tone={tone as Tone}>{msg}{link}</Notice>;
}

function QualityInner() {
  const { t, lang } = useI18n();
  const label = useLabel();
  const qc = useQueryClient();
  const { sp, set } = useUrlState();
  const health = useHealth();
  const entity = sp.get("entity") ?? "";
  const code = sp.get("code") ?? "";
  const status = sp.get("status") ?? "open";
  const runId = sp.get("run") ?? "";
  const search = sp.get("q") ?? "";
  const page = Number(sp.get("page") ?? "1");
  const openId = sp.get("qid");
  const [checkFilter, setCheckFilter] = useState<"failed" | "all">("failed");
  const [reprocess, setReprocess] = useState<null | { codes: string[] }>(null);

  const summary = useQuery({ queryKey: ["quality"], queryFn: () => api.get<Summary>("/api/quality") });
  const list = useQuery({
    queryKey: ["quarantine", entity, code, status, runId, search, page], placeholderData: keepPreviousData,
    queryFn: () => api.get<{ items: QRow[]; total: number; error_codes: string[] }>("/api/quality/quarantine",
      { entity: entity || undefined, error_code: code || undefined, status: status === "all" ? undefined : status, run_id: runId || undefined, q: search || undefined, page, page_size: 20 }),
  });
  const start = useMutation({
    mutationFn: (codes: string[]) => api.post<{ run_id: number }>("/api/pipeline/reprocess", { scope: "quarantine", error_codes: codes.length ? codes : undefined }),
    onSuccess: () => { setReprocess(null); qc.invalidateQueries({ queryKey: ["runs"] }); },
  });

  const s = summary.data;
  const byStatus = Object.fromEntries((s?.quarantine_by_status ?? []).map((x) => [x.status, x.records]));
  const openCodes = (s?.quarantine_by_code ?? []).filter((x) => x.status === "open");
  const maxCode = Math.max(1, ...openCodes.map((x) => x.records));
  const reasonsRef = useRef<HTMLUListElement>(null);
  useBarReveal(reasonsRef, [summary.data]);
  const checks = (s?.checks ?? []).filter((c) => checkFilter === "all" || c.status === "failed");
  const sep = lang === "ar" ? "، " : ", ";

  return (
    <>
      <PageHeader title={t("q.title")} description={t("q.desc")}
        actions={<Button variant="primary" onClick={() => setReprocess({ codes: [] })} disabled={!byStatus.open || health.data?.read_only}><RotateCcw className="size-4" aria-hidden /> {t("q.reprocessAll")}</Button>} />

      {start.isSuccess && <div className="mb-4" aria-live="polite"><ReprocessOutcome key={start.data.run_id} runId={start.data.run_id} /></div>}
      {start.isError && <div className="mb-4"><Notice tone="bad">{(start.error as ApiError).message}</Notice></div>}

      {summary.isError ? <ErrorState error={summary.error} onRetry={() => summary.refetch()} /> : !s ? <Loading /> : (
        <SpotlightPanel className="grid divide-y divide-line sm:grid-cols-4 sm:divide-x sm:divide-y-0 rtl:sm:divide-x-reverse">
          {[
            [t("q.passed"), s.checks_passed, t("q.passedHint", { id: s.run_id ?? "–" }), "text-ok"],
            [t("q.failed"), s.checks_failed, t("q.failedHint"), s.checks_failed ? "text-warn" : "text-ink"],
            [t("q.open"), byStatus.open ?? 0, t("q.openHint"), (byStatus.open ?? 0) ? "text-warn" : "text-ink"],
            [t("q.closed"), (byStatus.resolved ?? 0) + (byStatus.ignored ?? 0), t("q.closedHint", { n: count(byStatus.superseded ?? 0) }), "text-ink"],
          ].map(([lbl, value, hint, cls]) => (
            <div key={lbl as string} className="px-5 py-4">
              <p className="text-sm text-muted">{lbl}</p>
              <p className={clsx("num mt-1 text-[1.6rem] font-semibold", cls as string)}><Figure key={String(value)} value={value as number} kind="count" /></p>
              <p className="text-xs text-muted">{hint}</p>
            </div>
          ))}
        </SpotlightPanel>
      )}

      <div className="grid gap-x-8 lg:grid-cols-[1fr_1.3fr]">
        <Section title={t("q.byReason")} description={t("q.byReasonDesc")}>
          <Panel>
            {!s ? <Loading /> : openCodes.length === 0 ? <Empty title={t("q.nothing")}>{t("q.nothingBody")}</Empty> : (
              <ul ref={reasonsRef} className="max-h-[420px] divide-y divide-line overflow-auto">
                {openCodes.map((x) => (
                  <li key={`${x.error_code}-${x.entity}`} className="grid grid-cols-[1fr_2.5rem_7rem] items-center gap-3 px-4 py-2.5">
                    <button className="min-w-0 flex-1 text-start" onClick={() => set({ code: x.error_code, entity: x.entity, status: "open", page: null })}>
                      <span className="block text-sm font-medium hover:text-accent">{errorTitle(x.error_code, lang)}</span>
                      <span className="text-xs text-muted">{label("entity", x.entity)}</span>
                      <span className="mt-1 block h-1.5 rounded-full bg-hover"><span data-bar className="block h-1.5 rounded-full bg-warn/70" style={{ width: `${(x.records / maxCode) * 100}%` }} /></span>
                    </button>
                    <span className="num text-end text-sm font-medium">{count(x.records)}</span>
                    {REPROCESSABLE.has(x.error_code) ? (
                      <Button size="sm" variant="ghost" onClick={() => setReprocess({ codes: [x.error_code] })} disabled={health.data?.read_only}
                        aria-label={`${t("q.reprocess")} ${errorTitle(x.error_code, lang)}`}>{t("q.reprocess")}</Button>
                    ) : <span className="text-end text-xs text-faint" title={t("q.fixAtSourceTip")}>{t("q.fixAtSource")}</span>}
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </Section>

        <Section title={t("q.checks")} description={s?.run_id ? t("q.checksDesc", { id: s.run_id }) : undefined}
          actions={<Segmented label={t("q.showChecks")} value={checkFilter} onChange={setCheckFilter} options={[{ value: "failed", label: t("q.withFailures") }, { value: "all", label: t("common.all") }]} />}>
          <Panel className="overflow-hidden">
            {!s ? <Loading /> : checks.length === 0 ? <Empty title={checkFilter === "failed" ? t("q.noFailures") : t("q.noChecks")} /> : (
              <div className="max-h-[420px] overflow-auto">
                <table className="w-full">
                  <thead className="sticky top-0 border-b border-line bg-surface"><tr>
                    <th className={th}>{t("q.col.check")}</th><th className={th}>{t("q.col.level")}</th><th className={`${th} text-end`}>{t("q.col.failed")}</th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {checks.map((c, i) => (
                      <tr key={i}>
                        <td className={td}><p className="font-medium">{checkName(c.check_code, lang, c.check_name)}</p><p className="text-xs text-muted"><span dir="ltr">{c.check_code}</span>{sep}{label("entity", c.entity)}</p>
                          {c.details && <p className="text-xs text-muted" dir="ltr">{c.details}</p>}</td>
                        <td className={td}><Badge tone={c.check_level === "warehouse" ? "accent" : "neutral"}>{label("level", c.check_level)}</Badge></td>
                        <td className={`${td} num text-end`}><span className={c.failed_count ? "font-medium text-warn" : "text-ok"}>{count(c.failed_count)}</span> / {count(c.checked_count)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
          <p className="mt-2 text-xs text-muted">{t("q.checksNote")}</p>
        </Section>
      </div>

      <Section title={t("q.records")}>
        <Panel className="p-4">
          <div className="flex flex-wrap items-end gap-3">
            <Field label={t("q.status")} htmlFor="qs"><Select id="qs" value={status} onChange={(v) => set({ status: v, page: null })}
              options={[...["open", "ignored", "resolved", "superseded"].map((v) => ({ value: v, label: label("status", v) })), { value: "all", label: t("q.allStatuses") }]} /></Field>
            <Field label={t("q.entity")} htmlFor="qe"><Select id="qe" value={entity} onChange={(v) => set({ entity: v, page: null })}
              options={[{ value: "", label: t("q.allEntities") }, ...ENTITIES.map((e) => ({ value: e, label: label("entity", e) }))]} /></Field>
            <Field label={t("q.reason")} htmlFor="qc"><Select id="qc" value={code} onChange={(v) => set({ code: v, page: null })}
              options={[{ value: "", label: t("q.allReasons") }, ...(list.data?.error_codes ?? []).map((c) => ({ value: c, label: errorTitle(c, lang) }))]} /></Field>
            <Field label={t("q.search")} htmlFor="qq"><input id="qq" className={inputClass} defaultValue={search} maxLength={100}
              onKeyDown={(e) => e.key === "Enter" && set({ q: (e.target as HTMLInputElement).value, page: null })}
              onBlur={(e) => e.target.value !== search && set({ q: e.target.value, page: null })} /></Field>
            {runId && <Badge tone="info">{t("q.runFilter", { id: runId })} <button aria-label={t("q.clearRun")} onClick={() => set({ run: null })}>×</button></Badge>}
          </div>
        </Panel>
        <Panel className="mt-3 overflow-hidden">
          {list.isError ? <div className="p-4"><ErrorState error={list.error} onRetry={() => list.refetch()} /></div> : !list.data ? <Loading /> : list.data.items.length === 0 ? (
            <Empty title={t("q.noMatch")}>{status === "open" ? t("q.noOpen") : t("q.tryOther")}</Empty>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px]">
                  <thead className="border-b border-line bg-paper/60"><tr>
                    <th className={th}>{t("q.col.record")}</th><th className={th}>{t("q.col.reason")}</th><th className={th}>{t("q.col.source")}</th><th className={th}>{t("q.col.run")}</th><th className={th}>{t("q.col.status")}</th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {list.data.items.map((r) => (
                      <tr key={r.quarantine_id} className="cursor-pointer hover:bg-hover/60" onClick={() => set({ qid: String(r.quarantine_id) })}>
                        <td className={td}><button dir="ltr" className="font-medium text-accent hover:underline" onClick={(e) => { e.stopPropagation(); set({ qid: String(r.quarantine_id) }); }}>{r.source_record_id}</button>
                          <p className="text-xs text-muted">{label("entity", r.entity)}</p></td>
                        <td className={`${td} max-w-sm`}><p className="font-medium">{errorTitle(r.error_code, lang)}</p>
                          <p className="line-clamp-2 text-xs text-muted">{lang === "ar" && r.error_reason_ar ? r.error_reason_ar : r.error_reason}</p></td>
                        <td className={`${td} text-xs text-muted`} dir={r.source_file_name ? "ltr" : undefined}>{r.source_file_name ?? t("q.opsDb")}</td>
                        <td className={`${td} num`}>#{r.run_id}</td>
                        <td className={td}><Badge tone={Q_TONE[r.status]}>{label("status", r.status)}</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination page={page} pageSize={20} total={list.data.total} onPage={(p) => set({ page: String(p) })} />
            </>
          )}
        </Panel>
      </Section>

      <ConfirmDialog open={!!reprocess} title={t("q.confirmTitle")} confirmLabel={t("q.confirm")} busy={start.isPending}
        onCancel={() => setReprocess(null)} onConfirm={() => start.mutate(reprocess?.codes ?? [])}>
        {reprocess?.codes.length ? t("q.confirmCodes", { codes: reprocess.codes.map((c) => errorTitle(c, lang)).join(sep) }) : t("q.confirmAll")}{" "}
        {t("q.confirmTail")}
      </ConfirmDialog>
      <QuarantineDrawer id={openId ? Number(openId) : null} onClose={() => set({ qid: null })} />
    </>
  );
}

export default function QualityPage() {
  return <Suspense fallback={<Loading />}><QualityInner /></Suspense>;
}
