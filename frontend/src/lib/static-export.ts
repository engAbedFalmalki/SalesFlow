// Excel export for the static (Netlify) build: the same workbook as the API's
// GET /api/orders/export.xlsx (backend/salesflow/analytics/export.py), built in the browser.

import type { Workbook } from "exceljs";
import type { Filters } from "./hooks";
import type { StaticApi } from "./static-api";

type Lang = "en" | "ar";
type Row = ReturnType<StaticApi["exportRows"]>[number];
type Kind = "int" | "date" | "datetime" | "label" | "text" | "place" | "money";

// key, English header, Arabic header, kind, width (as in export.py)
const COLUMNS: [keyof Row | "order_placed_at", string, string, Kind, number][] = [
  ["order_id", "Order", "رقم الطلب", "int", 10],
  ["order_date", "Order date", "تاريخ الطلب", "date", 13],
  ["order_placed_at", "Placed at (Riyadh)", "وقت الطلب (الرياض)", "datetime", 18],
  ["status", "Status", "الحالة", "label", 13],
  ["channel", "Channel", "القناة", "label", 10],
  ["customer_code", "Customer code", "رمز العميل", "text", 13],
  ["customer_name", "Customer", "العميل", "text", 24],
  ["customer_region", "Region", "المنطقة", "place", 17],
  ["customer_city", "City", "المدينة", "place", 15],
  ["line_count", "Lines", "عدد الأسطر", "int", 9],
  ["units", "Units", "عدد الوحدات", "int", 9],
  ["gross_merchandise_value", "Gross merchandise value (SAR)", "إجمالي قيمة البضائع (ريال)", "money", 16],
  ["discounts", "Discounts (SAR)", "الخصومات (ريال)", "money", 13],
  ["ordered_value", "Ordered value (SAR)", "قيمة الطلب (ريال)", "money", 15],
  ["successful_collections", "Collected (SAR)", "المحصّل (ريال)", "money", 14],
  ["successful_refunds", "Refunded (SAR)", "المسترد (ريال)", "money", 14],
  ["net_collections", "Net collections (SAR)", "صافي التحصيل (ريال)", "money", 16],
  ["collection_state", "Collection status", "حالة التحصيل", "label", 16],
  ["transactions", "Transactions", "عدد العمليات", "int", 12],
];

const LABELS: Record<Lang, Record<string, string>> = {
  en: { pending: "Pending", processing: "Processing", shipped: "Shipped", delivered: "Delivered", cancelled: "Cancelled",
    app: "App", web: "Web", paid: "Paid", unpaid: "Unpaid", partially_refunded: "Partly refunded", refunded: "Refunded",
    partially_paid: "Partly paid" },
  ar: { pending: "بانتظار الدفع", processing: "قيد التجهيز", shipped: "تم الشحن", delivered: "تم التسليم", cancelled: "ملغى",
    app: "التطبيق", web: "الموقع", paid: "مدفوع", unpaid: "غير مدفوع", partially_refunded: "مسترد جزئياً", refunded: "مسترد",
    partially_paid: "مدفوع جزئياً" },
};

const ACCENT = "FF0E6A4E";
const FORMATS: Partial<Record<Kind, string>> = { int: "0", money: "#,##0.00", date: "yyyy-mm-dd", datetime: "yyyy-mm-dd hh:mm" };
const RIYADH_OFFSET_MS = 3 * 3_600_000; // Asia/Riyadh is UTC+3 all year

const isoDay = (s: string) => new Date(Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)));
const pad = (n: number) => String(n).padStart(2, "0");
const stamp = (d: Date) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;

export type ExportFilters = {
  date_from: string; date_to: string; status?: string[]; category?: string; product_sku?: string;
  region?: string; city?: string; order_id?: string;
};

export async function buildOrdersWorkbook(rows: Row[], f: ExportFilters, lang: Lang, filters: Filters | undefined, limit: number): Promise<Workbook> {
  const { default: ExcelJS } = await import("exceljs");
  const ar = lang === "ar";
  const placeNames = new Map<string, string>();
  for (const p of [...(filters?.regions ?? []), ...(filters?.cities ?? [])]) placeNames.set(p.value, p.ar);
  const place = (v: string | null) => (v && ar ? placeNames.get(v) ?? v : v);
  const all = ar ? "الكل" : "All";

  const wb = new ExcelJS.Workbook();
  const ws = wb.addWorksheet(ar ? "الطلبات" : "Orders", { views: [{ state: "frozen", ySplit: 1, rightToLeft: ar }] });
  ws.columns = COLUMNS.map(([, , , , w]) => ({ width: w + (ar ? 4 : 0) }));

  const header = ws.addRow(COLUMNS.map((c) => (ar ? c[2] : c[1])));
  header.height = 32;
  header.eachCell((c) => {
    c.font = { name: "Arial", bold: true, color: { argb: "FFFFFFFF" } };
    c.fill = { type: "pattern", pattern: "solid", fgColor: { argb: ACCENT } };
    c.alignment = { horizontal: "center", vertical: "middle", wrapText: true };
    c.border = { bottom: { style: "thin", color: { argb: "FFDCE2DE" } } };
  });

  for (const r of rows) {
    const values = COLUMNS.map(([key, , , kind]) => {
      if (key === "order_placed_at") return new Date(Date.parse(r.order_placed_at_utc) + RIYADH_OFFSET_MS);
      const v = r[key];
      if (kind === "date") return isoDay(String(v));
      if (kind === "money") return Number(v);
      if (kind === "label") return LABELS[lang][String(v)] ?? v;
      if (kind === "place") return place(v as string | null);
      return v;
    });
    const row = ws.addRow(values);
    row.eachCell({ includeEmpty: true }, (c, i) => {
      c.font = { name: "Arial" };
      const fmt = FORMATS[COLUMNS[i - 1][3]];
      if (fmt) c.numFmt = fmt;
    });
  }

  const n = rows.length;
  if (n) {
    const last = n + 1;
    ws.addRow([]);
    const totals = ws.addRow(COLUMNS.map(([key, , , kind], i) => {
      const letter = ws.getColumn(i + 1).letter;
      if (i === 0) return ar ? "الإجمالي" : "Total";
      if (kind === "money" || key === "line_count" || key === "units" || key === "transactions") return { formula: `SUBTOTAL(9,${letter}2:${letter}${last})` };
      if (key === "order_date") return { formula: `SUBTOTAL(3,A2:A${last})` };
      return null;
    }));
    totals.eachCell({ includeEmpty: true }, (c, i) => {
      c.font = { name: "Arial", bold: true };
      c.border = { top: { style: "medium", color: { argb: ACCENT } } };
      if (COLUMNS[i - 1][3] === "money") c.numFmt = FORMATS.money!;
    });
  }
  ws.autoFilter = { from: { row: 1, column: 1 }, to: { row: Math.max(n + 1, 2), column: COLUMNS.length } };

  const info = wb.addWorksheet(ar ? "معلومات" : "About", { views: [{ rightToLeft: ar }] });
  info.columns = [{ width: 38 }, { width: 70 }];
  info.addRow(["SalesFlow — " + (ar ? "تصدير الطلبات" : "Orders export")]).getCell(1).font = { name: "Arial", bold: true, size: 14, color: { argb: ACCENT } };
  info.addRow([]);
  const statuses = (f.status ?? []).map((s) => LABELS[lang][s] ?? s).join(", ") || all;
  const lines: [string, string][] = [
    [ar ? "وقت التصدير (الرياض)" : "Exported at (Asia/Riyadh)", stamp(new Date(Date.now() + RIYADH_OFFSET_MS))],
    [ar ? "عدد الطلبات" : "Orders", String(n)],
    [ar ? "الفترة (تاريخ الطلب، توقيت الرياض)" : "Period (order date, Asia/Riyadh)", `${f.date_from} → ${f.date_to}`],
    [ar ? "الحالة" : "Status", statuses],
    [ar ? "الفئة" : "Category", f.category || all],
    [ar ? "المنتج" : "Product", f.product_sku || all],
    [ar ? "المنطقة" : "Region", place(f.region ?? null) || all],
    [ar ? "المدينة" : "City", place(f.city ?? null) || all],
    [ar ? "رقم الطلب" : "Order number", f.order_id ? String(Number(f.order_id.replace("#", ""))) : "—"],
  ];
  if (n >= limit) {
    lines.push([ar ? "تنبيه" : "Note", ar ? `تم الوصول إلى الحد الأقصى (${limit} طلب)؛ ضيّق الفلاتر لتصدير الباقي.`
      : `The export limit (${limit} orders) was reached; narrow the filters to export the rest.`]);
  }
  lines.push(
    [ar ? "ملاحظة" : "Notes", ar
      ? "تُختار الطلبات حسب تاريخ الطلب. أعمدة التحصيل إجمالية لكل طلب مهما كان تاريخ الدفع. التحصيلات حركة نقد وليست إيراداً محاسبياً."
      : "Orders are selected by order date. Collection columns are lifetime totals per order, whatever the payment date. Collections are cash movements, not accounting revenue."],
    [ar ? "البيانات" : "Data", ar ? "بيانات اصطناعية مولّدة، لا تمثل أشخاصاً أو معاملات حقيقية." : "Synthetic, generated data. No real people or transactions."],
    [ar ? "العملة" : "Currency", "SAR"],
  );
  for (const [k, v] of lines) {
    const row = info.addRow([k, v]);
    row.getCell(1).font = { name: "Arial", bold: true };
    row.getCell(2).font = { name: "Arial" };
    row.getCell(2).alignment = { wrapText: true, vertical: "top" };
  }
  return wb;
}
