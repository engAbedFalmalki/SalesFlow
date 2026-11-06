"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Fragment, Suspense, useState } from "react";
import { Figure, SpotlightPanel } from "@/components/motion";
import { Badge, Empty, ErrorState, Field, Loading, PageHeader, Panel, RECON_TONE, Section, Select, td, th } from "@/components/ui";
import { api, type EntityStats } from "@/lib/api";
import type { DictKey } from "@/lib/dict";
import { en } from "@/lib/dict";
import { count, dateTime, humanize, money } from "@/lib/format";
import { useUrlState } from "@/lib/hooks";
import { useI18n, useLabel, type Lang } from "@/lib/i18n";

type Result = { seq: number; area: "rows" | "amounts" | "business"; entity: string; metric: string; source_label: string; warehouse_label: string; source_value: string; warehouse_value: string; difference: string; explained_difference: string; unexplained_difference: string; state: string; explanation: string; details: Record<string, unknown> | null };
type Resp = { run: { run_id: number; mode: string; finished_at_utc: string } | null; results: Result[]; entity_stats: EntityStats[]; runs: { run_id: number; mode: string; finished_at_utc: string; unresolved: number }[] };
type T = (k: DictKey, v?: Record<string, string | number>) => string;

function fmt(r: Result, v: string) {
  return r.area === "rows" ? count(Number(v)) : money(v);
}

function metricKey(r: Result): DictKey {
  if (r.area === "business") return "rm.business";
  if (r.area === "rows") return `rm.${r.entity}` as DictKey;
  if (r.entity === "order_item") return "rm.ordered";
  return r.metric.toLowerCase().includes("refund") ? "rm.refunds" : "rm.collections";
}

/** In Arabic, explanations are rebuilt from the structured values; English uses the backend text. */
function explanation(r: Result, t: T, lang: Lang): string {
  if (lang === "en") return r.explanation;
  const d = r.details ?? {};
  const explained = Number(r.explained_difference);
  if (r.area === "rows") {
    let s = Number(r.difference) === 0 ? t("rx.allLoaded") : t("rx.quarantinedRows", { n: count(explained) });
    const excl = Number(d.excluded_modified_after_window ?? 0);
    if (excl) s += t("rx.excluded", { n: count(excl) });
    return s;
  }
  if (r.area === "amounts" && r.entity === "order_item") {
    const n = r.explanation.match(/^(\d+) quarantined lines/)?.[1];
    return n ? t("rx.quarantinedLines", { n, amount: money(r.explained_difference) }) : t("rx.noQuarantinedLines");
  }
  if (r.area === "amounts") return explained ? t("rx.quarantinedTxns", { amount: money(r.explained_difference) }) : t("rx.noQuarantinedTxns");
  return t("rx.business", {
    n: count(Number(d.unpaid_active_orders ?? 0)), value: money(String(d.unpaid_active_ordered_value ?? "0")),
    refunds: money(String(d.refunds_on_active_orders ?? "0")), cancelled: money(String(d.net_collections_on_cancelled_orders ?? "0")),
  });
}

const LABELS: Record<string, DictKey> = {
  "Latest records in accepted files": "rl.srcFiles",
  "Σ qty × unit price − discount in source": "rl.srcAmount",
  "Σ FactSalesLine.NetAmount": "rl.whAmount",
  "Σ successful payments in accepted files": "rl.srcColl",
  "Σ successful refunds in accepted files": "rl.srcRef",
  "Σ successful payments": "rl.whColl",
  "Σ successful refunds": "rl.whRef",
};
function sideLabel(s: string, t: T, lang: Lang): string {
  if (lang === "en") return s;
  if (s.startsWith("Source rows (updated_at")) return t("rl.srcOps");
  const m = s.match(/^Rows in (.+)$/);
  if (m) return t("rl.whRows", { table: m[1] });
  return LABELS[s] ? t(LABELS[s]) : s;
}

function detailLabel(k: string, t: T): string {
  const key = `rd.${k}` as DictKey;
  return key in en ? t(key) : humanize(k);
}

function ResultTable({ rows, quarantineLink }: { rows: Result[]; quarantineLink: (r: Result) => string }) {
  const { t, lang } = useI18n();
  const label = useLabel();
  const [open, setOpen] = useState<number | null>(null);
  return (
    <Panel className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[860px]">
          <thead className="border-b border-line bg-paper/60"><tr>
            <th className={th}>{t("r.col.comparison")}</th><th className={`${th} text-end`}>{t("r.col.source")}</th><th className={`${th} text-end`}>{t("r.col.warehouse")}</th>
            <th className={`${th} text-end`}>{t("r.col.diff")}</th><th className={`${th} text-end`}>{t("r.col.explained")}</th><th className={th}>{t("r.col.state")}</th></tr></thead>
          <tbody className="divide-y divide-line">
            {rows.map((r) => (
              <Fragment key={r.seq}>
                <tr>
                  <td className={td}>
                    <button className="text-start font-medium hover:text-accent" aria-expanded={open === r.seq} onClick={() => setOpen(open === r.seq ? null : r.seq)}>{lang === "en" ? r.metric : t(metricKey(r))}</button>
                    <p className="text-xs text-muted">{explanation(r, t, lang)}</p>
                  </td>
                  <td className={`${td} num text-end`} title={sideLabel(r.source_label, t, lang)}>{fmt(r, r.source_value)}</td>
                  <td className={`${td} num text-end`} title={sideLabel(r.warehouse_label, t, lang)}>{fmt(r, r.warehouse_value)}</td>
                  <td className={`${td} num text-end`}>{Number(r.difference) === 0 ? "–" : fmt(r, r.difference)}</td>
                  <td className={`${td} num text-end`}>{Number(r.explained_difference) === 0 ? "–" : fmt(r, r.explained_difference)}</td>
                  <td className={td}><Badge tone={RECON_TONE[r.state]} title={t(`r.help.${r.state}` as DictKey)}>{label("status", r.state)}</Badge></td>
                </tr>
                {open === r.seq && (
                  <tr className="bg-paper/60">
                    <td colSpan={6} className="px-4 py-3 text-sm">
                      <p><span className="text-muted">{t("r.sourceIs")}</span> {sideLabel(r.source_label, t, lang)}. <span className="text-muted">{t("r.warehouseIs")}</span> {sideLabel(r.warehouse_label, t, lang)}.</p>
                      {r.details && Object.entries(r.details).map(([k, v]) => (
                        <p key={k} className="mt-1 text-xs"><span className="text-muted">{detailLabel(k, t)}:</span>{" "}
                          <span className="num" dir="ltr">{Array.isArray(v) ? (v.length ? v.join(", ") : t("r.none_list")) : String(v)}</span></p>
                      ))}
                      {r.state === "explained" && <Link className="mt-2 inline-block text-sm font-medium text-accent underline" href={quarantineLink(r)}>{t("r.seeQuarantine")}</Link>}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function ReconInner() {
  const { t, lang } = useI18n();
  const label = useLabel();
  const { sp, set } = useUrlState();
  const runId = sp.get("run") ?? "";
  const q = useQuery({ queryKey: ["recon", runId], queryFn: () => api.get<Resp>("/api/reconciliation", { run_id: runId || undefined }) });
  const d = q.data;
  const states = (d?.results ?? []).reduce<Record<string, number>>((a, r) => ({ ...a, [r.state]: (a[r.state] ?? 0) + 1 }), {});
  const biz = d?.results.find((r) => r.area === "business");
  const link = (r: Result) => `/quality?status=open&entity=${r.entity}`;
  const flowCols: DictKey[] = ["r.col.readSrc", "rd.col.new", "rd.col.unchanged", "rd.col.dup", "rd.col.accepted", "rd.col.rejected", "rd.col.inserted", "rd.col.updated", "r.col.identical"];

  return (
    <>
      <PageHeader title={t("r.title")} description={t("r.desc")}
        actions={d?.runs.length ? (
          <Field label={t("r.run")} htmlFor="rrun"><Select id="rrun" value={runId || String(d.run?.run_id ?? "")} onChange={(v) => set({ run: v })}
            options={d.runs.map((r) => ({ value: String(r.run_id), label: t("r.runOption", { id: r.run_id, mode: label("mode", r.mode), time: dateTime(r.finished_at_utc), unresolved: r.unresolved ? t("r.unresolvedN", { n: r.unresolved }) : "" }) }))} /></Field>
        ) : null} />

      {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !d ? <Loading /> : !d.run ? (
        <Panel><Empty title={t("r.none")}>{t("r.noneBody")}</Empty></Panel>
      ) : (
        <>
          <SpotlightPanel className="grid divide-y divide-line sm:grid-cols-4 sm:divide-x sm:divide-y-0 rtl:sm:divide-x-reverse">
            {(["matched", "explained", "unresolved", "informational"] as const).map((s) => (
              <div key={s} className="px-5 py-4">
                <Badge tone={RECON_TONE[s]}>{label("status", s)}</Badge>
                <p className="num mt-2 text-[1.6rem] font-semibold"><Figure key={`${s}-${states[s] ?? 0}`} value={states[s] ?? 0} kind="count" /></p>
                <p className="text-xs text-muted">{t(`r.help.${s}` as DictKey)}</p>
              </div>
            ))}
          </SpotlightPanel>
          <p className="mt-2 text-xs text-muted">{t("r.resultsFor")} <Link className="text-accent underline" href={`/pipeline?run=${d.run.run_id}`}>#{d.run.run_id}</Link>{" "}
            {t("r.publishedAt", { mode: label("mode", d.run.mode), time: dateTime(d.run.finished_at_utc) })}</p>

          <Section title={t("r.rows")} description={t("r.rowsDesc")}>
            <ResultTable rows={d.results.filter((r) => r.area === "rows")} quarantineLink={link} />
          </Section>
          <Section title={t("r.amounts")} description={t("r.amountsDesc")}>
            <ResultTable rows={d.results.filter((r) => r.area === "amounts")} quarantineLink={link} />
          </Section>

          {biz && (
            <Section title={t("r.bizTitle")} description={t("r.bizDesc")}>
              <Panel className="p-5">
                <div className="grid gap-6 sm:grid-cols-2">
                  <div className="basis-order pt-3"><p className="text-sm text-muted">{t("r.bizActive")}</p><p className="num mt-1 text-xl font-semibold">{money(biz.source_value)}</p></div>
                  <div className="basis-txn pt-3"><p className="text-sm text-muted">{t("r.bizNet")}</p><p className="num mt-1 text-xl font-semibold">{money(biz.warehouse_value)}</p></div>
                </div>
                <p className="mt-4 max-w-3xl text-sm leading-relaxed text-muted">{explanation(biz, t, lang)}</p>
                {biz.details && (
                  <dl className="num mt-4 grid gap-x-8 gap-y-1 text-sm sm:grid-cols-2">
                    {Object.entries(biz.details).map(([k, v]) => (
                      <div key={k} className="flex justify-between gap-4 border-b border-line py-1.5"><dt className="text-muted">{detailLabel(k, t)}</dt>
                        <dd>{typeof v === "number" ? count(v) : money(String(v))}</dd></div>
                    ))}
                  </dl>
                )}
              </Panel>
            </Section>
          )}

          <Section title={t("r.flow")} description={t("r.flowDesc")}>
            <Panel className="overflow-hidden">
              {d.entity_stats.length === 0 ? <Empty title={t("r.noStats")} /> : (
                <div className="overflow-x-auto">
                  <table className="num w-full min-w-[820px] text-end">
                    <thead className="border-b border-line bg-paper/60"><tr>
                      <th className={`${th} text-start`}>{t("rd.col.entity")}</th>
                      {flowCols.map((h) => <th key={h} className={`${th} text-end`}>{t(h)}</th>)}
                    </tr></thead>
                    <tbody className="divide-y divide-line">
                      {d.entity_stats.map((e) => (
                        <tr key={e.entity}>
                          <td className={`${td} text-start`}>{label("entity", e.entity)}</td>
                          {[e.rows_read, e.rows_new_version, e.rows_unchanged, e.rows_duplicate, e.rows_accepted, e.rows_rejected, e.rows_inserted, e.rows_updated, e.rows_dw_unchanged]
                            .map((v, i) => <td key={i} className={td}>{count(v)}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
          </Section>
        </>
      )}
    </>
  );
}

export default function ReconciliationPage() {
  return <Suspense fallback={<Loading />}><ReconInner /></Suspense>;
}
