"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { CircleCheck, LoaderCircle, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { latinDigits, money } from "@/lib/format";
import { useFilters, usePlaceName, useRunWatch } from "@/lib/hooks";
import { useI18n, useLabel } from "@/lib/i18n";
import { Button, Drawer, ErrorState, Field, inputClass, Notice, Segmented, Select } from "./ui";

type Customer = { customer_id: number; customer_code: string; full_name: string; city: string | null };
type Line = { key: number; sku: string; qty: string; discount: string };
type Result = {
  order_id: number; ordered_value: string; lines: number; payment_file: string | null; run_id: number | null;
  run_note?: { code: string; active_run_id: number };
};
const METHODS = ["mada", "credit_card", "apple_pay", "stc_pay", "cash_on_delivery"] as const;
const OUTCOMES = ["paid", "failed_then_paid", "failed", "unpaid"] as const;

/** Decimal string -> integer halalas (exact; no binary floating point). */
function cents(v: string): number | null {
  const s = v.trim();
  if (!s) return 0;
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return null;
  const [i, f = ""] = s.split(".");
  return Number(i) * 100 + Number(f.padEnd(2, "0"));
}
const fromCents = (c: number) => `${Math.floor(c / 100)}.${String(c % 100).padStart(2, "0")}`;

let nextKey = 1;
const newLine = (): Line => ({ key: nextKey++, sku: "", qty: "1", discount: "" });

export function PurchaseDrawer({ open, onClose, onOpenOrder }: { open: boolean; onClose: () => void; onOpenOrder: (id: number) => void }) {
  const { t } = useI18n();
  const label = useLabel();
  const place = usePlaceName();
  const qc = useQueryClient();
  const filters = useFilters();
  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [nc, setNc] = useState({ first_name: "", last_name: "", email: "", city: "" });
  const [lines, setLines] = useState<Line[]>([newLine()]);
  const [channel, setChannel] = useState<"app" | "web">("web");
  const [method, setMethod] = useState<(typeof METHODS)[number]>("mada");
  const [outcome, setOutcome] = useState<(typeof OUTCOMES)[number]>("paid");
  const [runPipeline, setRunPipeline] = useState(true);
  const [formError, setFormError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  useEffect(() => { const tm = setTimeout(() => setDebounced(query.trim()), 250); return () => clearTimeout(tm); }, [query]);
  useEffect(() => { if (method === "cash_on_delivery") setOutcome("unpaid"); }, [method]);

  const customers = useQuery({
    queryKey: ["customers", debounced], enabled: open && mode === "existing" && !result,
    queryFn: () => api.get<{ items: Customer[] }>("/api/customers", { q: debounced || undefined, limit: 8 }),
  });
  const products = useMemo(() => (filters.data?.products ?? []).filter((p) => p.is_active), [filters.data]);
  const priceOf = (sku: string) => products.find((p) => p.sku === sku)?.list_price;

  const lineInfo = lines.map((l) => {
    const price = priceOf(l.sku);
    const qty = /^\d+$/.test(l.qty) ? Number(l.qty) : NaN;
    const disc = cents(l.discount);
    const gross = price && Number.isInteger(qty) ? (cents(price) ?? 0) * qty : null;
    const bad = !l.sku || !(qty >= 1 && qty <= 99) || disc === null || (gross !== null && disc! > gross);
    return { gross, disc: disc ?? 0, bad };
  });
  const totalCents = lineInfo.reduce((s, x) => s + (x.gross !== null ? x.gross - x.disc : 0), 0);

  const submit = useMutation({
    mutationFn: () => api.post<Result>("/api/dev/purchases", {
      ...(mode === "existing" ? { customer_id: customer?.customer_id } : { new_customer: { ...nc, city: nc.city || undefined } }),
      lines: lines.map((l) => ({ sku: l.sku, quantity: Number(l.qty), discount_amount: l.discount.trim() || "0" })),
      channel, payment_method: method, payment_outcome: outcome, run_pipeline: runPipeline,
    }),
    onSuccess: (r) => { setResult(r); qc.invalidateQueries({ queryKey: ["runs"] }); qc.invalidateQueries({ queryKey: ["freshness"] }); },
  });

  const run = useRunWatch(result?.run_id);

  const reset = () => {
    setResult(null); setFormError(null); submit.reset(); setLines([newLine()]); setCustomer(null); setQuery("");
    setNc({ first_name: "", last_name: "", email: "", city: "" });
  };
  const close = () => { onClose(); setTimeout(reset, 200); };

  const onSubmit = () => {
    if (mode === "existing" ? !customer : !(nc.first_name && nc.last_name && nc.email)) return setFormError(t("p.needCustomer"));
    if (lineInfo.some((x) => x.bad)) return setFormError(t("p.needProduct"));
    setFormError(null);
    submit.mutate();
  };

  return (
    <Drawer open={open} onClose={close} title={t("p.title")} description={t("p.desc")}>
      {result ? (
        <div className="space-y-4">
          <div className="flex items-start gap-3 rounded-lg border border-ok/30 bg-ok-soft p-4">
            <CircleCheck className="mt-0.5 size-5 shrink-0 text-ok" aria-hidden />
            <div className="space-y-1 text-sm">
              <p className="font-semibold">{t("p.created", { id: result.order_id })}</p>
              <p className="num">{t("p.createdBody", { lines: result.lines, total: money(result.ordered_value) })}</p>
              <p>{result.payment_file ? t("p.fileWritten", { name: result.payment_file }) : t("p.noFile")}</p>
            </div>
          </div>
          {result.run_id ? (
            <Notice tone={run.data?.status === "failed" ? "bad" : run.data?.status === "succeeded" ? "ok" : "info"}>
              <span className="inline-flex items-center gap-2">
                {run.data?.status === "succeeded" ? t("p.runDone") : run.data?.status === "failed" ? t("p.runFailed") : (
                  <><LoaderCircle className="size-4 animate-spin" aria-hidden /> {t("p.runQueued", { id: result.run_id })} {t("p.runWait")}</>)}
              </span>
            </Notice>
          ) : result.run_note ? <Notice tone="warn">{t("p.runBusy", { id: result.run_note.active_run_id })}</Notice>
            : <Notice tone="info">{t("p.notLoadedYet")}</Notice>}
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" disabled={run.data?.status !== "succeeded"} onClick={() => onOpenOrder(result.order_id)}>{t("p.openOrder")}</Button>
            {result.run_id && <Link href={`/pipeline?run=${result.run_id}`} className="inline-flex h-10 items-center rounded-md border border-line-strong bg-surface px-4 text-sm font-medium hover:bg-hover">{t("p.openRun")}</Link>}
            <Button onClick={reset}>{t("p.another")}</Button>
          </div>
        </div>
      ) : (
        <form className="space-y-6" onSubmit={(e) => { e.preventDefault(); onSubmit(); }}>
          <section className="space-y-3" aria-label={t("p.customer")}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-semibold">{t("p.customer")}</h3>
              <Segmented label={t("p.customer")} value={mode} onChange={(m) => { setMode(m); setFormError(null); }}
                options={[{ value: "existing", label: t("p.existing") }, { value: "new", label: t("p.new") }]} />
            </div>
            {mode === "existing" ? (
              <div className="space-y-2">
                <input aria-label={t("p.search")} placeholder={t("p.search")} value={query} onChange={(e) => setQuery(e.target.value)} className={clsx(inputClass, "w-full")} />
                {customers.isError ? <ErrorState error={customers.error} /> : (
                  <ul className="max-h-48 divide-y divide-line overflow-auto rounded-md border border-line bg-surface" data-lenis-prevent>
                    {(customers.data?.items ?? []).map((c) => (
                      <li key={c.customer_id}>
                        <button type="button" onClick={() => setCustomer(c)} aria-pressed={customer?.customer_id === c.customer_id}
                          className={clsx("flex w-full items-center justify-between gap-3 px-3 py-2 text-start text-sm hover:bg-hover",
                            customer?.customer_id === c.customer_id && "bg-accent-soft text-accent-strong")}>
                          <span className="truncate">{c.full_name}</span>
                          <span className="num shrink-0 text-xs text-muted">{c.customer_code}{c.city ? `, ${place(c.city)}` : ""}</span>
                        </button>
                      </li>
                    ))}
                    {customers.data && customers.data.items.length === 0 && <li className="px-3 py-2 text-sm text-muted">{t("p.noCustomers")}</li>}
                  </ul>
                )}
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={t("p.first")} htmlFor="nc-first"><input id="nc-first" className={inputClass} maxLength={100} value={nc.first_name} onChange={(e) => setNc({ ...nc, first_name: e.target.value })} /></Field>
                <Field label={t("p.last")} htmlFor="nc-last"><input id="nc-last" className={inputClass} maxLength={100} value={nc.last_name} onChange={(e) => setNc({ ...nc, last_name: e.target.value })} /></Field>
                <Field label={t("p.email")} htmlFor="nc-email"><input id="nc-email" type="email" dir="ltr" className={inputClass} maxLength={200} value={nc.email} onChange={(e) => setNc({ ...nc, email: e.target.value })} /></Field>
                <Field label={t("p.city")} htmlFor="nc-city">
                  <Select id="nc-city" value={nc.city} onChange={(v) => setNc({ ...nc, city: v })}
                    options={[{ value: "", label: t("p.cityNone") }, ...(filters.data?.cities ?? []).map((c) => ({ value: c.value, label: `${place(c.value)} (${place(c.region)})` }))]} />
                </Field>
              </div>
            )}
          </section>

          <section className="space-y-3" aria-label={t("p.products")}>
            <h3 className="font-semibold">{t("p.products")}</h3>
            {lines.map((l, i) => {
              const info = lineInfo[i];
              const price = priceOf(l.sku);
              return (
                <div key={l.key} className="grid grid-cols-[1fr_5rem_7rem_auto] items-end gap-2 rounded-md border border-line bg-surface p-3 max-sm:grid-cols-2">
                  <Field label={t("od.product")} htmlFor={`p-${l.key}`}>
                    <select id={`p-${l.key}`} className={clsx(inputClass, "pe-8 max-sm:col-span-2")} value={l.sku}
                      onChange={(e) => setLines(lines.map((x) => (x.key === l.key ? { ...x, sku: e.target.value } : x)))}>
                      <option value="">{t("p.choose")}</option>
                      {products.map((p) => (
                        <option key={p.sku} value={p.sku} disabled={lines.some((x) => x.sku === p.sku && x.key !== l.key)}>{p.name} ({p.sku})</option>
                      ))}
                    </select>
                  </Field>
                  <Field label={t("p.qty")} htmlFor={`q-${l.key}`}>
                    <input id={`q-${l.key}`} dir="ltr" inputMode="numeric" className={clsx(inputClass, "num")} value={l.qty}
                      onChange={(e) => setLines(lines.map((x) => (x.key === l.key ? { ...x, qty: latinDigits(e.target.value).replace(/\D/g, "").slice(0, 2) } : x)))} />
                  </Field>
                  <Field label={t("p.discount")} htmlFor={`d-${l.key}`}>
                    <input id={`d-${l.key}`} dir="ltr" inputMode="decimal" placeholder="0.00" className={clsx(inputClass, "num", info.gross !== null && info.disc > info.gross && "border-bad")} value={l.discount}
                      onChange={(e) => setLines(lines.map((x) => (x.key === l.key ? { ...x, discount: latinDigits(e.target.value).replace(/[^\d.]/g, "") } : x)))} />
                  </Field>
                  <Button type="button" variant="ghost" aria-label={t("p.remove")} disabled={lines.length === 1}
                    onClick={() => setLines(lines.filter((x) => x.key !== l.key))}><Trash2 className="size-4" /></Button>
                  {price && <p className="num col-span-full -mt-1 text-xs text-muted">{t("p.price", { price: money(price) })}{info.gross !== null && ` → ${money(fromCents(info.gross - info.disc))}`}</p>}
                </div>
              );
            })}
            <Button type="button" size="sm" disabled={lines.length >= 10} onClick={() => setLines([...lines, newLine()])}><Plus className="size-4" aria-hidden /> {t("p.addLine")}</Button>
          </section>

          <section className="grid gap-3 sm:grid-cols-3" aria-label={t("p.payment")}>
            <Field label={t("p.channel")} htmlFor="p-channel">
              <Select id="p-channel" value={channel} onChange={(v) => setChannel(v as "app" | "web")}
                options={[{ value: "web", label: label("channel", "web") }, { value: "app", label: label("channel", "app") }]} />
            </Field>
            <Field label={t("p.payment")} htmlFor="p-method">
              <Select id="p-method" value={method} onChange={(v) => setMethod(v as (typeof METHODS)[number])}
                options={METHODS.map((m) => ({ value: m, label: label("method", m) }))} />
            </Field>
            <Field label={t("p.outcome")} htmlFor="p-outcome">
              <select id="p-outcome" className={clsx(inputClass, "pe-8")} value={outcome} disabled={method === "cash_on_delivery"}
                onChange={(e) => setOutcome(e.target.value as (typeof OUTCOMES)[number])}>
                {OUTCOMES.map((o) => <option key={o} value={o}>{label("p.out", o)}</option>)}
              </select>
            </Field>
            {method === "cash_on_delivery" && <p className="text-xs text-muted sm:col-span-3">{t("p.codNote")}</p>}
          </section>

          <label className="flex items-start gap-3 text-sm">
            <input type="checkbox" className="mt-0.5 size-4 accent-[var(--color-accent)]" checked={runPipeline} onChange={(e) => setRunPipeline(e.target.checked)} />
            <span><span className="font-medium">{t("p.run")}</span><span className="block text-xs text-muted">{t("p.runNote")}</span></span>
          </label>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
            <p className="text-sm text-muted">{t("p.total")} <b className="num text-lg text-ink">{money(fromCents(totalCents))}</b></p>
            <Button type="submit" variant="primary" busy={submit.isPending}>{t("p.submit")}</Button>
          </div>
          {formError && <p role="alert" className="text-sm text-bad">{formError}</p>}
          {submit.isError && <ErrorState error={submit.error as ApiError} />}
        </form>
      )}
    </Drawer>
  );
}
