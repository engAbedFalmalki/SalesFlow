"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import Link from "next/link";
import { Suspense, useRef, useState } from "react";
import { TrendChart } from "@/components/charts";
import { DateRangeControl } from "@/components/date-range";
import { Figure, SpotlightPanel, useBarReveal } from "@/components/motion";
import { Empty, ErrorState, InfoTip, Loading, PageHeader, Panel, Section, Segmented, Select, Skeleton } from "@/components/ui";
import { api, type Freshness, type Metric, type Overview, type SeriesPoint } from "@/lib/api";
import type { DictKey } from "@/lib/dict";
import { bizDate, count, dateTime, money, moneyCompact, relativeMinutes } from "@/lib/format";
import { useDateRange, useFilters, useUrlState } from "@/lib/hooks";
import { useI18n, useLabel } from "@/lib/i18n";

type Kpi = { key: string; label: DictKey; help: DictKey; kind: "money" | "count"; lowerIsBetter?: boolean };
const SALES: Kpi[] = [
  { key: "active_ordered_value", label: "kpi.active", help: "kpi.active.help", kind: "money" },
  { key: "distinct_active_orders", label: "kpi.orders", help: "kpi.orders.help", kind: "count" },
  { key: "average_order_value", label: "kpi.aov", help: "kpi.aov.help", kind: "money" },
];
const COLLECTIONS: Kpi[] = [
  { key: "successful_collections", label: "kpi.coll", help: "kpi.coll.help", kind: "money" },
  { key: "successful_refunds", label: "kpi.refunds", help: "kpi.refunds.help", kind: "money", lowerIsBetter: true },
  { key: "net_collections", label: "kpi.net", help: "kpi.net.help", kind: "money" },
];

function Delta({ m, kind, available, lowerIsBetter }: { m: Metric; kind: Kpi["kind"]; available: boolean; lowerIsBetter?: boolean }) {
  const { t } = useI18n();
  if (!available) return <span className="text-faint">{t("ov.noComparison")}</span>;
  if (m.change === null) return <span className="text-faint">{t("common.na")}</span>;
  const up = Number(m.change) > 0;
  const flat = Number(m.change) === 0;
  const good = lowerIsBetter ? !up : up;
  const pct = m.change_pct === null ? "" : ` (${up ? "+" : ""}${m.change_pct}%)`;
  return (
    <span className={clsx("inline-flex gap-1", flat ? "text-muted" : good ? "text-ok" : "text-bad")}>
      <bdi>{kind === "money" ? money(m.change, { sign: true }) : `${up ? "+" : ""}${count(m.change as number)}`}</bdi>
      {pct && <bdi dir="ltr">{pct.trim()}</bdi>}
    </span>
  );
}

function KpiGroup({ title, basis, kpis, data }: { title: string; basis: "order" | "txn"; kpis: Kpi[]; data: Overview }) {
  const { t } = useI18n();
  return (
    <div className={clsx("min-w-0 px-5 pb-5 pt-4", basis === "order" ? "basis-order" : "basis-txn")}>
      <p className="text-sm font-medium text-muted">{title}</p>
      <div className="mt-3 grid gap-5 sm:grid-cols-3">
        {kpis.map((k) => {
          const m = data.metrics[k.key];
          return (
            <div key={k.key} className="min-w-0">
              <div className="flex items-center gap-1 text-[0.82rem] text-muted">{t(k.label)}<InfoTip label={t("common.about", { name: t(k.label) })}>{t(k.help)}</InfoTip></div>
              <p className="num mt-1 truncate text-[1.6rem] font-semibold leading-tight tracking-[-0.01em]" title={k.kind === "money" ? money(m.value) : undefined}>
                <Figure key={`${String(m.value)}`} value={m.value} kind={k.kind} />
              </p>
              <p className="num mt-1 text-xs"><Delta m={m} kind={k.kind} available={data.comparison.available} lowerIsBetter={k.lowerIsBetter} /></p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FreshnessLine() {
  const { t } = useI18n();
  const label = useLabel();
  const { data } = useQuery({ queryKey: ["freshness"], queryFn: () => api.get<Freshness>("/api/freshness"), refetchInterval: 20_000 });
  if (!data) return <Skeleton className="h-4 w-80" />;
  const ok = data.last_successful_run;
  return (
    <span>
      {ok ? <>{t("ov.refreshedBy", { when: relativeMinutes(ok.minutes_ago) })}{" "}
        <Link className="font-medium text-accent underline-offset-2 hover:underline" href={`/pipeline?run=${ok.run_id}`}>{t("ov.run", { id: ok.run_id })}</Link>{" "}
        ({label("mode", ok.mode)}).{" "}</> : <>{t("ov.notLoaded")} </>}
      {data.coverage.max_date && t("ov.through", { date: bizDate(data.coverage.max_date) })}
    </span>
  );
}

function TopProducts({ from, to }: { from: string; to: string }) {
  const { t } = useI18n();
  const [cat, setCat] = useState("");
  const q = useQuery({
    queryKey: ["top", from, to, cat],
    queryFn: () => api.get<{ products: { sku: string; name: string; category: string; units: number; active_ordered_value: string; share_pct_of_all: string }[] }>(
      "/api/products/top", { date_from: from, date_to: to, category: cat || undefined, limit: 8 }),
  });
  const filters = useFilters();
  const max = Math.max(1, ...(q.data?.products.map((p) => Number(p.active_ordered_value)) ?? [1]));
  const listRef = useRef<HTMLOListElement>(null);
  useBarReveal(listRef, [q.data]);
  return (
    <Section title={t("ov.topTitle")} description={t("ov.topDesc")}
      actions={<Select ariaLabel={t("ov.category")} value={cat} onChange={setCat} className="h-9"
        options={[{ value: "", label: t("ov.allCategories") }, ...(filters.data?.categories ?? []).map((c) => ({ value: c, label: c }))]} />}>
      <Panel>
        {q.isLoading ? <Loading /> : q.isError ? <div className="p-4"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
          : !q.data?.products.length ? <Empty title={t("ov.noSales")} />
          : (
            <ol ref={listRef} className="divide-y divide-line">
              {q.data.products.map((p, i) => (
                <li key={p.sku} className="grid grid-cols-[1.5rem_1fr_auto] items-center gap-3 px-4 py-2.5">
                  <span className="num text-sm text-faint">{i + 1}</span>
                  <div className="min-w-0">
                    <Link href={`/sales?from=${from}&to=${to}&sku=${p.sku}`} className="block truncate text-sm font-medium hover:text-accent">{p.name}</Link>
                    <div className="mt-1 h-1.5 rounded-full bg-hover"><div data-bar className="h-1.5 rounded-full bg-accent" style={{ width: `${(Number(p.active_ordered_value) / max) * 100}%` }} /></div>
                    <p className="mt-1 text-xs text-muted">{t("ov.productMeta", { category: p.category, units: count(p.units), share: p.share_pct_of_all })}</p>
                  </div>
                  <span className="num text-sm font-medium" title={money(p.active_ordered_value)}>{moneyCompact(p.active_ordered_value)}</span>
                </li>
              ))}
            </ol>
          )}
      </Panel>
    </Section>
  );
}

function StatusDistribution({ from, to }: { from: string; to: string }) {
  const { t } = useI18n();
  const label = useLabel();
  const q = useQuery({ queryKey: ["status", from, to], queryFn: () => api.get<{ items: { status: string; orders: number; ordered_value: string }[] }>("/api/orders/status-distribution", { date_from: from, date_to: to }) });
  const total = q.data?.items.reduce((s, x) => s + x.orders, 0) ?? 0;
  const barRef = useRef<HTMLDivElement>(null);
  useBarReveal(barRef, [q.data]);
  const color: Record<string, string> = { delivered: "#0e6a4e", shipped: "#5b8f7c", processing: "#a8c7b9", pending: "#d6a64a", cancelled: "#b42318" };
  return (
    <Section title={t("ov.statusTitle")} description={t("ov.statusDesc")}>
      <Panel className="p-4">
        {q.isLoading ? <Loading /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : total === 0 ? <Empty title={t("ov.noOrders")} /> : (
          <>
            <div ref={barRef} className="flex h-3 overflow-hidden rounded-full" role="img" aria-label={t("ov.statusShare")}>
              {q.data!.items.map((s) => <div data-bar key={s.status} style={{ width: `${(s.orders / total) * 100}%`, background: color[s.status] }} />)}
            </div>
            <table className="mt-4 w-full text-sm">
              <thead className="sr-only"><tr><th>{t("sales.col.status")}</th><th>{t("kpi.orders")}</th><th>%</th><th>{t("sales.col.ordered")}</th></tr></thead>
              <tbody>
                {q.data!.items.map((s) => (
                  <tr key={s.status} className="border-t border-line first:border-0">
                    <td className="py-2"><span className="me-2 inline-block size-2.5 rounded-full align-middle" style={{ background: color[s.status] }} />{label("status", s.status)}</td>
                    <td className="num py-2 text-end">{count(s.orders)}</td>
                    <td className="num py-2 text-end text-muted">{((s.orders / total) * 100).toFixed(1)}%</td>
                    <td className="num py-2 text-end" title={money(s.ordered_value)}>{moneyCompact(s.ordered_value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {q.data!.items.some((s) => s.status === "cancelled") && <p className="mt-3 text-xs text-muted">{t("ov.cancelNote")}</p>}
          </>
        )}
      </Panel>
    </Section>
  );
}

function OverviewInner() {
  const { t } = useI18n();
  const { from, to, min, max, setRange, filters } = useDateRange(30);
  const { sp, set } = useUrlState();
  const grain = (sp.get("grain") as "day" | "week" | "month") ?? "day";
  const ready = !!(from && to);
  const ov = useQuery({ queryKey: ["overview", from, to], enabled: ready, queryFn: () => api.get<Overview>("/api/overview", { date_from: from, date_to: to }) });
  const ts = useQuery({ queryKey: ["ts", from, to, grain], enabled: ready, queryFn: () => api.get<{ points: SeriesPoint[] }>("/api/timeseries", { date_from: from, date_to: to, grain }) });

  if (filters.isError) return <><PageHeader title={t("ov.title")} /><ErrorState error={filters.error} onRetry={() => filters.refetch()} /></>;
  if (filters.data && !max) {
    return (
      <>
        <PageHeader title={t("ov.title")} />
        <Panel><Empty title={t("ov.empty")}><Link className="text-accent underline" href="/pipeline">{t("ov.emptyBody")}</Link></Empty></Panel>
      </>
    );
  }
  return (
    <>
      <PageHeader hero title={t("ov.title")} description={<FreshnessLine />}
        actions={ready && min && max ? <DateRangeControl from={from!} to={to!} min={min} max={max} onChange={setRange} /> : <Skeleton className="h-10 w-96" />} />

      {ov.isError ? <ErrorState error={ov.error} onRetry={() => ov.refetch()} /> : (
        <SpotlightPanel className="overflow-hidden">
          {!ov.data ? <div className="grid gap-4 p-5 sm:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-16" />)}</div> : (
            <>
              <div className="grid divide-y divide-line xl:grid-cols-2 xl:divide-x xl:divide-y-0 rtl:xl:divide-x-reverse">
                <KpiGroup title={t("ov.salesGroup")} basis="order" kpis={SALES} data={ov.data} />
                <KpiGroup title={t("ov.collGroup")} basis="txn" kpis={COLLECTIONS} data={ov.data} />
              </div>
              <p className="border-t border-line bg-paper/60 px-5 py-2.5 text-xs text-muted">
                {ov.data.comparison.available
                  ? t("ov.compare", { days: ov.data.comparison.days, from: bizDate(ov.data.comparison.date_from), to: bizDate(ov.data.comparison.date_to) })
                  : t("ov.noCompare", { date: bizDate(ov.data.coverage.min_date) })}
                {" "}{t("ov.dateNote")}
              </p>
            </>
          )}
        </SpotlightPanel>
      )}

      <Section title={t("ov.trendTitle")} description={t("ov.trendDesc")}
        actions={<Segmented label={t("ov.grain")} value={grain} onChange={(g) => set({ grain: g })}
          options={[{ value: "day", label: t("ov.day") }, { value: "week", label: t("ov.week") }, { value: "month", label: t("ov.month") }]} />}>
        <Panel className="p-4">
          {ts.isLoading || !ready ? <Skeleton className="h-72" /> : ts.isError ? <ErrorState error={ts.error} onRetry={() => ts.refetch()} />
            : <TrendChart points={ts.data!.points} grain={grain} />}
        </Panel>
      </Section>

      {ready && (
        <div className="grid gap-x-8 lg:grid-cols-[1.4fr_1fr]">
          <TopProducts from={from!} to={to!} />
          <StatusDistribution from={from!} to={to!} />
        </div>
      )}
      {ov.data && <p className="mt-8 text-xs text-faint">{t("ov.footer", { time: dateTime(new Date().toISOString()) })}</p>}
    </>
  );
}

export default function OverviewPage() {
  return <Suspense fallback={<Loading />}><OverviewInner /></Suspense>;
}
