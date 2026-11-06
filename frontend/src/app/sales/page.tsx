"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowDown, ArrowUp, ChevronsUpDown, FileSpreadsheet, Plus, Search } from "lucide-react";
import { Suspense, useEffect, useState } from "react";
import { DateRangeControl } from "@/components/date-range";
import { PrimaryAction } from "@/components/motion";
import { OrderDrawer } from "@/components/order-drawer";
import { PurchaseDrawer } from "@/components/purchase-drawer";
import { Badge, Button, COLLECTION_TONE, Empty, ErrorState, Field, inputClass, Loading, ORDER_TONE, PageHeader, Pagination, Panel, Select, Skeleton, ToggleChip, td, th } from "@/components/ui";
import { api, downloadStaticOrdersXlsx, STATIC_DATA } from "@/lib/api";
import type { DictKey } from "@/lib/dict";
import { bizDate, count, latinDigits, money } from "@/lib/format";
import { useDateRange, useHealth, usePlaceName, useUrlState } from "@/lib/hooks";
import { useI18n, useLabel } from "@/lib/i18n";

type Row = { order_id: number; order_date: string; status: string; customer_code: string; customer_name: string; customer_region: string | null; customer_city: string | null; line_count: number; units: number; ordered_value: string; net_collections: string; collection_state: string };
type Resp = { items: Row[]; total: number; page: number; page_size: number; totals: { orders: number; ordered_value: string; active_ordered_value: string; net_collections: string } };

const STATUSES = ["pending", "processing", "shipped", "delivered", "cancelled"];
const SORTS: { key: string; label: DictKey; end?: boolean }[] = [
  { key: "order_id", label: "sales.col.order" }, { key: "order_date", label: "sales.col.date" }, { key: "customer", label: "sales.col.customer" },
  { key: "status", label: "sales.col.status" }, { key: "ordered_value", label: "sales.col.ordered", end: true },
  { key: "net_collections", label: "sales.col.net", end: true },
];

function SalesInner() {
  const { t, lang } = useI18n();
  const label = useLabel();
  const place = usePlaceName();
  const health = useHealth();
  const { from, to, min, max, setRange, filters } = useDateRange(30);
  const { sp, set } = useUrlState();
  const statuses = sp.getAll("status");
  const category = sp.get("category") ?? "";
  const sku = sp.get("sku") ?? "";
  const region = sp.get("region") ?? "";
  const city = sp.get("city") ?? "";
  const orderParam = sp.get("q") ?? "";
  const sort = sp.get("sort") ?? "order_date";
  const dir = (sp.get("dir") as "asc" | "desc") ?? "desc";
  const page = Number(sp.get("page") ?? "1");
  const openOrder = sp.get("order");
  const [purchaseOpen, setPurchaseOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [search, setSearch] = useState(orderParam);
  useEffect(() => setSearch(orderParam), [orderParam]);
  useEffect(() => {
    const tm = setTimeout(() => { if (search !== orderParam && (search === "" || /^#?\d{1,10}$/.test(search))) set({ q: search, page: null }); }, 350);
    return () => clearTimeout(tm);
  }, [search, orderParam, set]);
  const searchInvalid = search !== "" && !/^#?\d{1,10}$/.test(search);

  const params = { date_from: from, date_to: to, status: statuses, category: category || undefined, product_sku: sku || undefined, region: region || undefined, city: city || undefined, order_id: orderParam ? orderParam.replace("#", "") : undefined };
  const q = useQuery({
    queryKey: ["orders", params, sort, dir, page], enabled: !!(from && to), placeholderData: keepPreviousData,
    queryFn: () => api.get<Resp>("/api/orders", { ...params, sort, direction: dir, page, page_size: 25 }),
  });
  const products = (filters.data?.products ?? []).filter((p) => !category || p.category === category);
  const cities = (filters.data?.cities ?? []).filter((c) => !region || c.region === region);
  const canPurchase = health.data && !health.data.read_only && health.data.env !== "production";

  const toggleSort = (key: string) => set({ sort: key, dir: sort === key && dir === "desc" ? "asc" : "desc", page: null });
  const toggleStatus = (s: string) => set({ status: statuses.includes(s) ? statuses.filter((x) => x !== s) : [...statuses, s], page: null });
  const anyFilter = statuses.length || category || sku || region || city || orderParam;
  const exportParams = { ...params, lang };

  return (
    <>
      <PageHeader title={t("sales.title")} description={t("sales.desc")}
        actions={from && to && min && max ? <DateRangeControl from={from} to={to} min={min} max={max} onChange={setRange} /> : <Skeleton className="h-10 w-96" />} />

      <Panel className="p-4">
        <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
          <Field label={t("sales.orderNumber")} htmlFor="order-search">
            <div className="relative">
              <Search className="pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
              <input id="order-search" dir="ltr" inputMode="numeric" placeholder="1001" value={search} onChange={(e) => setSearch(latinDigits(e.target.value).trim())}
                aria-invalid={searchInvalid} aria-describedby={searchInvalid ? "search-err" : undefined}
                className={clsx(inputClass, "num w-40 ps-9 text-start", searchInvalid && "border-bad")} />
            </div>
          </Field>
          <Field label={t("sales.category")} htmlFor="cat">
            <Select id="cat" value={category} onChange={(v) => set({ category: v, sku: null, page: null })}
              options={[{ value: "", label: t("ov.allCategories") }, ...(filters.data?.categories ?? []).map((c) => ({ value: c, label: c }))]} />
          </Field>
          <Field label={t("sales.product")} htmlFor="sku">
            <Select id="sku" value={sku} onChange={(v) => set({ sku: v, page: null })} className="max-w-[260px]"
              options={[{ value: "", label: t("sales.allProducts") }, ...products.map((p) => ({ value: p.sku, label: `${p.name} (${p.sku})` }))]} />
          </Field>
          <Field label={t("sales.region")} htmlFor="region">
            <Select id="region" value={region} onChange={(v) => set({ region: v, city: null, page: null })}
              options={[{ value: "", label: t("sales.allRegions") }, ...(filters.data?.regions ?? []).map((r) => ({ value: r.value, label: lang === "ar" ? r.ar : r.en }))]} />
          </Field>
          <Field label={t("sales.city")} htmlFor="city">
            <Select id="city" value={city} onChange={(v) => set({ city: v, page: null })}
              options={[{ value: "", label: t("sales.allCities") }, ...cities.map((c) => ({ value: c.value, label: lang === "ar" ? c.ar : c.en }))]} />
          </Field>
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 text-[0.8rem] font-medium text-muted">{t("sales.status")}</legend>
            <div className="flex flex-wrap gap-1.5">
              {STATUSES.map((s) => <ToggleChip key={s} pressed={statuses.includes(s)} onClick={() => toggleStatus(s)}>{label("status", s)}</ToggleChip>)}
            </div>
          </fieldset>
          {anyFilter ? <button className="h-10 text-sm font-medium text-accent hover:underline" onClick={() => set({ status: [], category: null, sku: null, region: null, city: null, q: null, page: null })}>{t("sales.clear")}</button> : null}
        </div>
        {searchInvalid && <p id="search-err" className="mt-2 text-xs text-bad">{t("sales.digitsOnly")}</p>}
      </Panel>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <p className="num text-sm text-muted" aria-live="polite">
          {q.data ? t("sales.summary", {
            n: count(q.data.total), orders: q.data.total === 1 ? t("sales.order1") : t("sales.orderN"),
            ordered: money(q.data.totals.ordered_value), active: money(q.data.totals.active_ordered_value), net: money(q.data.totals.net_collections),
          }) : " "}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {canPurchase && (
            <Button onClick={() => setPurchaseOpen(true)}><Plus className="size-4" aria-hidden /> {t("sales.addPurchase")}</Button>
          )}
          {STATIC_DATA ? (
            <PrimaryAction busy={exporting} disabled={!q.data?.total} onClick={() => {
              setExporting(true);
              downloadStaticOrdersXlsx({ ...params, date_from: from!, date_to: to!, status: statuses, order_id: params.order_id }, lang, filters.data)
                .finally(() => setExporting(false));
            }}>
              <FileSpreadsheet className="size-4" aria-hidden /> {t("sales.exportXlsx")}
            </PrimaryAction>
          ) : (
            <PrimaryAction href={from && to ? api.url("/api/orders/export.xlsx", exportParams) : ""} disabled={!q.data?.total}>
              <FileSpreadsheet className="size-4" aria-hidden /> {t("sales.exportXlsx")}
            </PrimaryAction>
          )}
        </div>
      </div>

      <Panel className="mt-3 overflow-hidden">
        {q.isError ? <div className="p-4"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div> : !q.data ? <Loading label={t("sales.loading")} /> : q.data.items.length === 0 ? (
          <Empty title={t("sales.noMatch")}>{t("sales.noMatchBody")}</Empty>
        ) : (
          <>
            <div className={clsx("overflow-x-auto", q.isFetching && "opacity-60")}>
              <table className="w-full min-w-[820px]">
                <thead className="border-b border-line bg-paper/60">
                  <tr>
                    {SORTS.map((c) => (
                      <th key={c.key} className={clsx(th, c.end && "text-end")} aria-sort={sort === c.key ? (dir === "asc" ? "ascending" : "descending") : "none"}>
                        <button onClick={() => toggleSort(c.key)} className={clsx("inline-flex items-center gap-1 hover:text-ink", sort === c.key && "text-ink")}>
                          {t(c.label)}
                          {sort === c.key ? (dir === "asc" ? <ArrowUp className="size-3.5" /> : <ArrowDown className="size-3.5" />) : <ChevronsUpDown className="size-3.5 opacity-50" />}
                        </button>
                      </th>
                    ))}
                    <th className={th}>{t("sales.col.items")}</th>
                    <th className={th}>{t("sales.col.collection")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {q.data.items.map((r) => (
                    <tr key={r.order_id} className="cursor-pointer hover:bg-hover/60" onClick={() => set({ order: String(r.order_id) })}>
                      <td className={td}><button className="num font-medium text-accent hover:underline" onClick={(e) => { e.stopPropagation(); set({ order: String(r.order_id) }); }}>#{r.order_id}</button></td>
                      <td className={`${td} num whitespace-nowrap`}>{bizDate(r.order_date)}</td>
                      <td className={td}><span className="block max-w-[200px] truncate">{r.customer_name}</span><span className="text-xs text-muted">{r.customer_code}{r.customer_city ? ` · ${place(r.customer_city)}` : ""}</span></td>
                      <td className={td}><Badge tone={ORDER_TONE[r.status]}>{label("status", r.status)}</Badge></td>
                      <td className={`${td} num text-end`}>{money(r.ordered_value, { currency: false })}</td>
                      <td className={`${td} num text-end`}>{money(r.net_collections, { currency: false })}</td>
                      <td className={`${td} num text-muted`}>{t("sales.items", { lines: r.line_count, units: r.units })}</td>
                      <td className={td}><Badge tone={COLLECTION_TONE[r.collection_state]}>{label("coll", r.collection_state)}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={page} pageSize={25} total={q.data.total} onPage={(p) => set({ page: String(p) })} />
          </>
        )}
      </Panel>
      <p className="mt-3 text-xs text-faint">{t("sales.footer")}</p>
      <OrderDrawer orderId={openOrder ? Number(openOrder) : null} onClose={() => set({ order: null })} />
      <PurchaseDrawer open={purchaseOpen} onClose={() => setPurchaseOpen(false)}
        onOpenOrder={(id) => { setPurchaseOpen(false); set({ order: String(id) }); }} />
    </>
  );
}

export default function SalesPage() {
  return <Suspense fallback={<Loading />}><SalesInner /></Suspense>;
}
