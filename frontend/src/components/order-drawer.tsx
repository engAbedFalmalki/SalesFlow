"use client";

import { useQuery } from "@tanstack/react-query";
import { MapPin } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import { bizDate, count, dateTime, money } from "@/lib/format";
import { usePlaceName } from "@/lib/hooks";
import { errorTitle, useI18n, useLabel } from "@/lib/i18n";
import { Badge, COLLECTION_TONE, Drawer, ErrorState, KeyValue, Loading, Notice, ORDER_TONE, RUN_TONE, td, th } from "./ui";

type Detail = {
  order: { order_id: number; order_placed_at_utc: string; order_date: string; status: string; channel: string; customer_code: string; customer_name: string; customer_city: string | null; customer_region: string | null; updated_run_id: number };
  lines: { order_item_id: number; line_number: number; sku: string; product_name: string; category: string; quantity: number; unit_price: string; current_list_price: string; discount_amount: string; gross_amount: string; net_amount: string }[];
  transactions: { transaction_id: string; transaction_type: string; status: string; amount: string; payment_method: string; transaction_at_utc: string; transaction_date: string; refers_to_transaction_id: string | null; source_file: string }[];
  totals: Record<string, string>;
  quarantined_records: { quarantine_id: number; entity: string; source_record_id: string; error_code: string; error_reason: string; error_reason_ar: string | null }[];
};

export function OrderDrawer({ orderId, onClose }: { orderId: number | null; onClose: () => void }) {
  const { t, lang } = useI18n();
  const label = useLabel();
  const place = usePlaceName();
  const q = useQuery({ queryKey: ["order", orderId], enabled: orderId !== null, queryFn: () => api.get<Detail>(`/api/orders/${orderId}`) });
  const d = q.data;
  const priceChanged = d?.lines.some((l) => l.unit_price !== l.current_list_price);
  const sep = lang === "ar" ? "، " : ", ";
  return (
    <Drawer open={orderId !== null} onClose={onClose} title={t("od.title", { id: orderId ?? "" })}
      description={d ? t("od.placed", { name: d.order.customer_name, code: d.order.customer_code, time: dateTime(d.order.order_placed_at_utc) }) : undefined}>
      {q.isLoading ? <Loading /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : d && (
        <div className="space-y-7">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={ORDER_TONE[d.order.status]}>{label("status", d.order.status)}</Badge>
            <Badge tone={COLLECTION_TONE[d.totals.collection_state]}>{label("coll", d.totals.collection_state)}</Badge>
            <span className="text-sm text-muted">{t("od.meta", { date: bizDate(d.order.order_date), channel: label("channel", d.order.channel), run: d.order.updated_run_id })}</span>
            {d.order.customer_city && (
              <span className="inline-flex items-center gap-1 text-sm text-muted">
                <MapPin className="size-3.5" aria-hidden />
                {place(d.order.customer_city)}{d.order.customer_region ? `${sep}${place(d.order.customer_region)}` : ""}
              </span>
            )}
          </div>

          {d.quarantined_records.length > 0 && (
            <Notice tone="warn">
              {t("od.quarantined", { n: d.quarantined_records.length })}{" "}
              {d.quarantined_records.map((r) => (
                <Link key={r.quarantine_id} className="underline" href={`/quality?qid=${r.quarantine_id}`}>
                  {label("entity", r.entity)} {r.source_record_id} ({errorTitle(r.error_code, lang)})
                </Link>
              )).reduce<ReactNode[]>((a, x, i) => (i ? [...a, sep, x] : [x]), [])}
            </Notice>
          )}

          <section aria-label={t("od.items")}>
            <h3 className="mb-2 font-semibold">{t("od.items")}</h3>
            <div className="overflow-x-auto rounded-lg border border-line bg-surface">
              <table className="w-full min-w-[560px]">
                <thead className="border-b border-line"><tr>
                  <th className={th}>{t("od.product")}</th><th className={`${th} text-end`}>{t("od.qty")}</th><th className={`${th} text-end`}>{t("od.unitPrice")}</th>
                  <th className={`${th} text-end`}>{t("od.discount")}</th><th className={`${th} text-end`}>{t("od.lineTotal")}</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {d.lines.map((l) => (
                    <tr key={l.order_item_id}>
                      <td className={td}><p className="font-medium">{l.product_name}</p><p className="text-xs text-muted">{l.sku}{sep}{l.category}</p></td>
                      <td className={`${td} num text-end`}>{count(l.quantity)}</td>
                      <td className={`${td} num text-end`}>{money(l.unit_price, { currency: false })}
                        {l.unit_price !== l.current_list_price && <p className="text-xs text-muted">{t("od.now", { price: money(l.current_list_price, { currency: false }) })}</p>}</td>
                      <td className={`${td} num text-end`}>{Number(l.discount_amount) ? `−${money(l.discount_amount, { currency: false })}` : "–"}</td>
                      <td className={`${td} num text-end font-medium`}>{money(l.net_amount, { currency: false })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {priceChanged && <p className="mt-2 text-xs text-muted">{t("od.priceNote")}</p>}
          </section>

          <div className="grid gap-6 sm:grid-cols-2">
            <section aria-label={t("od.orderValue")} className="basis-order rounded-lg border border-line bg-surface p-4">
              <h3 className="mb-3 font-semibold">{t("od.orderValue")}</h3>
              <KeyValue items={[[t("od.gmv"), money(d.totals.gross_merchandise_value)], [t("od.discounts"), `−${money(d.totals.discounts)}`],
                [<b key="o">{t("od.ordered")}</b>, <b key="ov">{money(d.totals.ordered_value)}</b>]]} />
            </section>
            <section aria-label={t("od.collections")} className="basis-txn rounded-lg border border-line bg-surface p-4">
              <h3 className="mb-3 font-semibold">{t("od.collections")}</h3>
              <KeyValue items={[[t("od.payments"), money(d.totals.successful_collections)], [t("od.refunds"), `−${money(d.totals.successful_refunds)}`],
                [<b key="n">{t("od.net")}</b>, <b key="nv">{money(d.totals.net_collections)}</b>]]} />
            </section>
          </div>

          <section aria-label={t("od.history")}>
            <h3 className="mb-2 font-semibold">{t("od.history")}</h3>
            {d.transactions.length === 0 ? <p className="text-sm text-muted">{t("od.noTxns")}</p> : (
              <ol className="relative space-y-3 border-s border-line ps-5">
                {d.transactions.map((tx) => (
                  <li key={tx.transaction_id} className="relative">
                    <span className={`absolute -start-[26px] top-1.5 size-2.5 rounded-full ring-4 ring-paper ${tx.status === "succeeded" ? (tx.transaction_type === "refund" ? "bg-collect" : "bg-accent") : "bg-line-strong"}`} />
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <p className="text-sm font-medium">{label("txn", tx.transaction_type)} <span dir="ltr">{tx.transaction_id}</span>
                        <span className="ms-2"><Badge tone={RUN_TONE[tx.status] ?? (tx.status === "pending" ? "warn" : "neutral")}>{label("status", tx.status)}</Badge></span></p>
                      <p className={`num text-sm font-medium ${tx.status !== "succeeded" ? "text-faint line-through" : ""}`}>{tx.transaction_type === "refund" ? "−" : ""}{money(tx.amount)}</p>
                    </div>
                    <p className="text-xs text-muted">
                      {t("od.txnMeta", { time: dateTime(tx.transaction_at_utc), method: label("method", tx.payment_method) })}
                      {tx.refers_to_transaction_id ? `${sep}${t("od.refunds_of", { id: tx.refers_to_transaction_id })}` : ""}. {t("od.file", { name: tx.source_file })}
                    </p>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      )}
    </Drawer>
  );
}
