"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { SeriesPoint } from "@/lib/api";
import { bizDate, count, money, monthYear } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

const ACCENT = "#0e6a4e";
const COLLECT = "#3e5368";

function axisMoney(v: number): string {
  const a = Math.abs(v);
  return a >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` : a >= 1_000 ? `${(v / 1_000).toFixed(1)}K` : String(v);
}

function TipBox({ active, payload, grain }: { active?: boolean; payload?: { payload: SeriesPoint }[]; grain: string }) {
  const { t, dir } = useI18n();
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  const label = grain === "day" ? bizDate(p.bucket) : grain === "week" ? t("chart.weekOf", { date: bizDate(p.bucket) }) : monthYear(p.bucket);
  return (
    <div dir={dir} className="num rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-lg">
      <p className="mb-1.5 font-medium text-ink">{label}</p>
      <p className="flex items-center gap-2 text-muted"><span className="mark-order" /> {t("chart.tipOrdered")} <b className="ms-auto ps-4 text-ink">{money(p.active_ordered_value)}</b></p>
      <p className="ms-6 text-muted">{t("chart.tipOrders", { n: count(p.orders) })}</p>
      <p className="mt-1 flex items-center gap-2 text-muted"><span className="mark-txn" /> {t("chart.tipNet")} <b className="ms-auto ps-4 text-ink">{money(p.net_collections)}</b></p>
      <p className="ms-6 text-muted">{t("chart.tipColl", { coll: money(p.successful_collections), ref: money(p.successful_refunds) })}</p>
    </div>
  );
}

export function TrendChart({ points, grain }: { points: SeriesPoint[]; grain: string }) {
  const { t } = useI18n();
  const data = points.map((p) => ({ ...p, _ordered: Number(p.active_ordered_value), _net: Number(p.net_collections) }));
  const fmtX = (b: string) => (grain === "month" ? monthYear(b) : bizDate(b, true));
  return (
    <figure>
      {/* Time runs left to right in both languages. */}
      <div dir="ltr" className="h-72 w-full" role="img" aria-label={t("chart.aria")}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
            <CartesianGrid stroke="#e7ebe8" vertical={false} />
            <XAxis dataKey="bucket" tickFormatter={fmtX} tick={{ fontSize: 12, fill: "#5a6a63" }} tickLine={false} axisLine={{ stroke: "#c3ccc7" }} minTickGap={24} />
            <YAxis tickFormatter={axisMoney} tick={{ fontSize: 12, fill: "#5a6a63" }} tickLine={false} axisLine={false} width={52} />
            <Tooltip content={<TipBox grain={grain} />} cursor={{ stroke: "#c3ccc7" }} />
            <Line type="monotone" dataKey="_ordered" stroke={ACCENT} strokeWidth={2.25} dot={false} isAnimationActive={false} name="ordered" />
            <Line type="monotone" dataKey="_net" stroke={COLLECT} strokeWidth={2.25} strokeDasharray="6 4" dot={false} isAnimationActive={false} name="net" />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
        <span className="flex items-center gap-2"><span className="mark-order" /> {t("chart.ordered")}</span>
        <span className="flex items-center gap-2"><span className="mark-txn" /> {t("chart.net")}</span>
      </figcaption>
    </figure>
  );
}
