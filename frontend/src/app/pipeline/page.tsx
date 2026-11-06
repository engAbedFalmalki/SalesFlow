"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Play } from "lucide-react";
import { Suspense, useEffect, useState } from "react";
import { RunDrawer, StageList, useDescribeParams } from "@/components/run-drawer";
import { Badge, Button, ConfirmDialog, Empty, ErrorState, Loading, Notice, PageHeader, Pagination, Panel, RUN_TONE, Section, td, th } from "@/components/ui";
import { api, ApiError, type RunDetail, type RunSummary } from "@/lib/api";
import { SCENARIOS_AR, type DictKey } from "@/lib/dict";
import { dateTime, seconds } from "@/lib/format";
import { useHealth, useUrlState } from "@/lib/hooks";
import { translate, useI18n, useLabel } from "@/lib/i18n";

type Scenario = { id: string; title: string; description: string; steps: { action: string; mode?: string; params?: Record<string, unknown> }[]; files: string[]; ops_changes: Record<string, number> };

function ActiveRun({ id, onOpen }: { id: number; onOpen: () => void }) {
  const { t } = useI18n();
  const label = useLabel();
  const q = useQuery({ queryKey: ["run", id], queryFn: () => api.get<RunDetail>(`/api/pipeline/runs/${id}`), refetchInterval: 1500 });
  const r = q.data;
  if (!r) return null;
  const done = r.stages.filter((s) => s.status === "succeeded").length;
  const total = r.stages.filter((s) => s.status !== "skipped").length;
  const current = r.stages.find((s) => s.status === "running");
  return (
    <Panel className="basis-order mb-6 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div aria-live="polite">
          <p className="font-semibold">{t("pl.isRunning", { id: r.run_id, status: label("status", r.status) })} <Badge tone={RUN_TONE[r.status]}>{label("mode", r.mode)}</Badge></p>
          <p className="text-sm text-muted">
            {r.status === "queued" ? t("pl.waiting") : t("pl.progress", {
              done, total, elapsed: seconds(r.duration_seconds),
              current: current ? t("pl.now", { stage: label("stage", current.stage_name) }) : "",
            })}
          </p>
        </div>
        <Button size="sm" onClick={onOpen}>{t("pl.openDetails")}</Button>
      </div>
      <div className="mt-4"><StageList run={r} /></div>
    </Panel>
  );
}

function Scenarios({ disabled }: { disabled: boolean }) {
  const { t, lang } = useI18n();
  const label = useLabel();
  const describe = useDescribeParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["scenarios"], queryFn: () => api.get<{ enabled: boolean; items: Scenario[] }>("/api/dev/scenarios") });
  const [result, setResult] = useState<{ id: string; ok: boolean; rows?: number; files?: number; error?: string } | null>(null);
  const apply = useMutation({
    mutationFn: (id: string) => api.post<{ ops: Record<string, number>; files: string[] | number }>(`/api/dev/scenarios/${id}/apply`, {}),
    onSuccess: (d, id) => {
      const rows = Object.values(d.ops ?? {}).reduce((a, b) => a + (typeof b === "number" ? b : 0), 0);
      setResult({ id, ok: true, rows, files: Array.isArray(d.files) ? d.files.length : Number(d.files) });
      qc.invalidateQueries({ queryKey: ["freshness"] });
    },
    onError: (e, id) => setResult({ id, ok: false, error: (e as ApiError).message }),
  });
  if (!q.data?.enabled) return null;
  const sep = lang === "ar" ? "، ثم " : ", then ";
  const next = (s: Scenario) => s.steps.map((st) => st.action === "retry_last_failed" ? t("pl.thenRetry") :
    `${t("pl.thenRun", { mode: label("mode", st.mode ?? "") })}${st.params ? ` (${describe(st.params)})` : ""}`).join(sep);
  return (
    <Section title={t("pl.simTitle")} description={t("pl.simDesc")}>
      <Panel>
        <ul className="divide-y divide-line">
          {q.data.items.map((s, i) => {
            const text = lang === "ar" && SCENARIOS_AR[s.id] ? SCENARIOS_AR[s.id] : s;
            return (
              <li key={s.id} className="grid gap-3 px-4 py-3 sm:grid-cols-[2rem_1fr_auto] sm:items-center">
                <span className="num text-sm text-faint">{i + 1}</span>
                <div>
                  <p className="text-sm font-medium">{text.title}</p>
                  <p className="text-xs leading-relaxed text-muted">{text.description} {t("pl.then", { steps: next(s) })}</p>
                  {result?.id === s.id && (
                    <p className={`mt-1 text-xs ${result.ok ? "text-ok" : "text-bad"}`} role="status">
                      {result.ok ? t("pl.applied", { rows: result.rows ?? 0, files: result.files ?? 0 }) : result.error}
                    </p>
                  )}
                </div>
                <Button size="sm" onClick={() => apply.mutate(s.id)} busy={apply.isPending && apply.variables === s.id} disabled={disabled}>
                  <FlaskConical className="size-4" aria-hidden /> {t("pl.apply")}
                </Button>
              </li>
            );
          })}
        </ul>
      </Panel>
    </Section>
  );
}

function PipelineInner() {
  const { t, lang } = useI18n();
  const label = useLabel();
  const describe = useDescribeParams();
  const qc = useQueryClient();
  const { sp, set } = useUrlState();
  const page = Number(sp.get("page") ?? "1");
  const openRun = sp.get("run");
  const health = useHealth();
  const [confirm, setConfirm] = useState<null | "initial" | "crash">(null);
  const runs = useQuery({
    queryKey: ["runs", page], placeholderData: keepPreviousData,
    queryFn: () => api.get<{ items: RunSummary[]; total: number }>("/api/pipeline/runs", { page, page_size: 15 }),
    refetchInterval: (query) => (query.state.data?.items.some((r) => r.status === "queued" || r.status === "running") ? 1500 : 10_000),
  });
  const active = runs.data?.items.find((r) => r.status === "queued" || r.status === "running");
  const activeId = active?.run_id;
  const [lastActive, setLastActive] = useState<number | undefined>(undefined);
  useEffect(() => {
    // When a run finishes, everything derived from the warehouse may have changed.
    if (lastActive !== undefined && activeId === undefined) qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "runs" });
    setLastActive(activeId);
  }, [activeId, lastActive, qc]);
  const start = useMutation({
    mutationFn: (body: { mode: string; fail_at?: string }) => api.post<{ run_id: number }>("/api/pipeline/runs", body),
    onSuccess: () => { setConfirm(null); qc.invalidateQueries({ queryKey: ["runs"] }); qc.invalidateQueries({ queryKey: ["freshness"] }); },
  });
  const readOnly = health.data?.read_only ?? false;
  const busy = !!active || start.isPending;
  const errorText = (r: RunSummary) => {
    if (!r.error) return "";
    if (lang === "en") return `${r.error.code.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase())}: ${r.error.message}`;
    return `${translate(lang, `err.${r.error.code}` as DictKey)}: ${translate(lang, `err.${r.error.code}.msg` as DictKey)}`;
  };

  return (
    <>
      <PageHeader title={t("pl.title")} description={t("pl.desc")}
        actions={<>
          <Button onClick={() => setConfirm("crash")} disabled={busy || readOnly || health.data?.env === "production"}>{t("pl.runCrash")}</Button>
          <Button onClick={() => setConfirm("initial")} disabled={busy || readOnly}>{t("pl.runFull")}</Button>
          <Button variant="primary" onClick={() => start.mutate({ mode: "incremental" })} busy={start.isPending && start.variables?.mode === "incremental"} disabled={busy || readOnly}>
            <Play className="size-4 rtl:rotate-180" aria-hidden /> {t("pl.runInc")}
          </Button>
        </>} />

      {readOnly && <div className="mb-4"><Notice tone="warn">{t("pl.readOnly")}</Notice></div>}
      {start.isError && <div className="mb-4"><Notice tone="bad">{(start.error as ApiError).message}</Notice></div>}
      {active && <ActiveRun id={active.run_id} onOpen={() => set({ run: String(active.run_id) })} />}

      <Panel className="overflow-hidden">
        {runs.isError ? <div className="p-4"><ErrorState error={runs.error} onRetry={() => runs.refetch()} /></div> : !runs.data ? <Loading label={t("pl.loading")} /> :
          runs.data.items.length === 0 ? <Empty title={t("pl.empty")}>{t("pl.emptyBody")}</Empty> : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px]">
                  <thead className="border-b border-line bg-paper/60"><tr>
                    <th className={th}>{t("pl.col.run")}</th><th className={th}>{t("pl.col.type")}</th><th className={th}>{t("pl.col.status")}</th><th className={th}>{t("pl.col.requested")}</th>
                    <th className={`${th} text-end`}>{t("pl.col.duration")}</th><th className={th}>{t("pl.col.stages")}</th><th className={th}>{t("pl.col.outcome")}</th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {runs.data.items.map((r) => (
                      <tr key={r.run_id} className="cursor-pointer hover:bg-hover/60" onClick={() => set({ run: String(r.run_id) })}>
                        <td className={td}><button className="num font-medium text-accent hover:underline" onClick={(e) => { e.stopPropagation(); set({ run: String(r.run_id) }); }}>#{r.run_id}</button></td>
                        <td className={td}>{label("mode", r.mode)}{r.retry_of_run_id ? <span className="text-xs text-muted"> {t("pl.retryOf", { id: r.retry_of_run_id })}</span> : null}
                          {describe(r.params) && <p className="text-xs text-muted">{describe(r.params)}</p>}</td>
                        <td className={td}><Badge tone={RUN_TONE[r.status]}>{label("status", r.status)}</Badge></td>
                        <td className={`${td} num whitespace-nowrap`}>{dateTime(r.requested_at_utc)}</td>
                        <td className={`${td} num text-end`}>{r.started_at_utc ? seconds(r.duration_seconds) : "–"}</td>
                        <td className={`${td} num`}>{t("pl.stagesOf", { done: r.stages_succeeded, total: r.stages_total })}</td>
                        <td className={`${td} max-w-xs`}>{r.error ? <span className="line-clamp-2 text-bad">{errorText(r)}</span>
                          : r.status === "succeeded" ? <span className="text-muted">{t("pl.published")}</span> : <span className="text-muted">{t("pl.inProgress")}</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination page={page} pageSize={15} total={runs.data.total} onPage={(p) => set({ page: String(p) })} />
            </>
          )}
      </Panel>

      <Scenarios disabled={readOnly} />

      <ConfirmDialog open={confirm === "initial"} title={t("pl.fullTitle")} confirmLabel={t("pl.fullConfirm")} busy={start.isPending}
        onCancel={() => setConfirm(null)} onConfirm={() => start.mutate({ mode: "initial" })}>
        {t("pl.fullBody")}
      </ConfirmDialog>
      <ConfirmDialog open={confirm === "crash"} title={t("pl.crashTitle")} confirmLabel={t("pl.crashConfirm")} busy={start.isPending}
        onCancel={() => setConfirm(null)} onConfirm={() => start.mutate({ mode: "incremental", fail_at: "before_commit" })}>
        {t("pl.crashBody")}
      </ConfirmDialog>
      <RunDrawer runId={openRun ? Number(openRun) : null} onClose={() => set({ run: null })} />
    </>
  );
}

export default function PipelinePage() {
  return <Suspense fallback={<Loading />}><PipelineInner /></Suspense>;
}
