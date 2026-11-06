// In-browser stand-in for the SalesFlow API, used by the static (Netlify) build.
//
// Data comes from backend/scripts/export_static.py: "snapshots" are exact API responses for
// endpoints without free-form filters; "datasets" are compact arrays from which the date-range
// endpoints are computed here with the same rules as backend/salesflow/analytics/queries.py.
// Money is handled as integer halalas (cents), so sums are exact; rounding follows the API.
// Read-only: every write answers 403 READ_ONLY, like the hosted API.
//
// Plain TypeScript with no imports so tests can run it under Node (scripts/compare-static.ts).

/* eslint-disable @typescript-eslint/no-explicit-any */
type Json = any;
type Params = Record<string, string | number | boolean | string[] | null | undefined>;

export type StaticDatasets = {
  customers: [string, string, string | null, string | null][];                 // code, name, city, region
  products: [string, string, string, number][];                                 // sku, name, category, list price
  orders: [number, string, string, string, string, number, number][];           // id, placed utc, date, status, channel, customer, run
  lines: [number, number, number, number, number, number, number, number, number][]; // order, item, line, product, qty, unit, disc, gross, net
  txns: [number, string, string, string, number, string | null, string, string, string | null, string | null][]; // order, id, type, status, amount, method, at, date, refers, file
  order_quarantine: Record<string, Json[]>;
};

export class StaticApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// ---------------------------------------------------------------- number helpers

/** Cents -> "1234.50" (like Python's format(Decimal, "f") at scale 2). */
export const m2 = (c: number): string => {
  const neg = c < 0;
  const a = Math.abs(c);
  return `${neg ? "-" : ""}${Math.floor(a / 100)}.${String(a % 100).padStart(2, "0")}`;
};

/** round(a / b) half up for a >= 0, b > 0 (Python ROUND_HALF_UP on positive values). */
const divHalfUp = (a: number, b: number) => Math.floor((2 * a + b) / (2 * b));

/** Decimal(diff / prev * 100).quantize(Decimal("0.1")) with Python's default ROUND_HALF_EVEN. */
function pct1(diff: number, prev: number): string {
  const num = diff * 1000;
  const den = prev;
  const sign = (num < 0) !== (den < 0) ? -1 : 1;
  const n = Math.abs(num), d = Math.abs(den);
  let q = Math.floor(n / d);
  const r = n - q * d;
  if (2 * r > d || (2 * r === d && q % 2 === 1)) q += 1;
  const neg = sign < 0 && num !== 0;
  return `${neg ? "-" : ""}${Math.floor(q / 10)}.${q % 10}`;
}

/** CAST(x * 100.0 / total AS DECIMAL(9,2)): two decimals, half away from zero. */
function share2(part: number, total: number): string | null {
  if (total === 0) return null;
  const q = divHalfUp(part * 10000, total);
  return `${Math.floor(q / 100)}.${String(q % 100).padStart(2, "0")}`;
}

// ---------------------------------------------------------------- dates (ISO yyyy-mm-dd, UTC arithmetic)

const dayMs = 86_400_000;
const toDay = (s: string) => Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) / dayMs;
const fromDay = (n: number) => new Date(n * dayMs).toISOString().slice(0, 10);
const weekStart = (s: string) => { const n = toDay(s); return fromDay(n - new Date(n * dayMs).getUTCDay()); }; // weeks start Sunday
const monthStart = (s: string) => `${s.slice(0, 7)}-01`;

function need(params: Params, key: string): string {
  const v = params[key];
  if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(v)) throw new StaticApiError(422, "VALIDATION_ERROR", `${key} is required (YYYY-MM-DD).`);
  return v;
}
const list = (v: Params[string]): string[] => (v === undefined || v === null || v === "" ? [] : Array.isArray(v) ? v : [String(v)]);

// ---------------------------------------------------------------- the API

export function createStaticApi(snap: Json, data: StaticDatasets, now: () => number = Date.now) {
  const { customers, products, orders, lines, txns } = data;

  // Per-order aggregates (lifetime collections, like the API's order listing).
  const linesBy: number[][] = orders.map(() => []);
  lines.forEach((l, i) => linesBy[l[0]].push(i));
  const txnsBy: number[][] = orders.map(() => []);
  txns.forEach((t, i) => txnsBy[t[0]].push(i));
  const agg = orders.map((_, oi) => {
    let gmv = 0, net = 0, units = 0, coll = 0, refunds = 0;
    for (const li of linesBy[oi]) { gmv += lines[li][7]; net += lines[li][8]; units += lines[li][4]; }
    for (const ti of txnsBy[oi]) {
      const t = txns[ti];
      if (t[3] === "succeeded" && t[2] === "payment") coll += t[4];
      if (t[3] === "succeeded" && t[2] === "refund") refunds += t[4];
    }
    return { gmv, net, units, lineCount: linesBy[oi].length, coll, refunds, txnCount: txnsBy[oi].length };
  });
  const byOrderId = new Map(orders.map((o, i) => [o[0], i]));
  const cancelled = (oi: number) => orders[oi][3] === "cancelled";

  // ---- overview / timeseries / top products / status distribution
  function salesMetrics(a: string, b: string) {
    let gmv = 0, ordered = 0, active = 0, units = 0;
    const activeOrders = new Set<number>(), cancelledOrders = new Set<number>();
    for (const l of lines) {
      const d = orders[l[0]][2];
      if (d < a || d > b) continue;
      gmv += l[7]; ordered += l[8];
      if (cancelled(l[0])) cancelledOrders.add(l[0]);
      else { active += l[8]; units += l[4]; activeOrders.add(l[0]); }
    }
    return {
      gross_merchandise_value: { c: gmv }, ordered_value: { c: ordered }, active_ordered_value: { c: active },
      distinct_active_orders: { n: activeOrders.size }, cancelled_orders: { n: cancelledOrders.size }, units: { n: units },
      average_order_value: activeOrders.size ? { c: divHalfUp(active, activeOrders.size) } : { c: null },
    } as Record<string, { c?: number | null; n?: number }>;
  }
  function collectionMetrics(a: string, b: string) {
    let coll = 0, refunds = 0, failed = 0;
    for (const t of txns) {
      if (t[7] < a || t[7] > b) continue;
      if (t[2] === "payment" && t[3] === "succeeded") coll += t[4];
      if (t[2] === "refund" && t[3] === "succeeded") refunds += t[4];
      if (t[2] === "payment" && t[3] === "failed") failed += 1;
    }
    return { successful_collections: { c: coll }, successful_refunds: { c: refunds }, net_collections: { c: coll - refunds },
      failed_payment_attempts: { n: failed } } as Record<string, { c?: number | null; n?: number }>;
  }
  function compare(cur: Record<string, { c?: number | null; n?: number }>, prev: typeof cur | null) {
    const out: Record<string, Json> = {};
    for (const [k, v] of Object.entries(cur)) {
      const isMoney = "c" in v;
      const val = isMoney ? v.c ?? null : v.n ?? null;
      const p = prev ? (isMoney ? prev[k].c ?? null : prev[k].n ?? null) : null;
      const fmt = (x: number | null) => (x === null ? null : isMoney ? m2(x) : x);
      const e: Json = { value: fmt(val), previous: fmt(p), change: null, change_pct: null };
      if (val !== null && p !== null) {
        e.change = fmt(val - p);
        e.change_pct = p === 0 ? null : pct1(val - p, p);
      }
      out[k] = e;
    }
    return out;
  }
  const coverage = () => snap.filters.coverage;

  function overview(p: Params) {
    const a = need(p, "date_from"), b = need(p, "date_to");
    const days = toDay(b) - toDay(a) + 1;
    const prevTo = fromDay(toDay(a) - 1), prevFrom = fromDay(toDay(a) - days);
    const cov = coverage();
    const available = !!cov.min_date && prevFrom >= cov.min_date;
    const cur = { ...salesMetrics(a, b), ...collectionMetrics(a, b) };
    const prev = available ? { ...salesMetrics(prevFrom, prevTo), ...collectionMetrics(prevFrom, prevTo) } : null;
    return {
      period: { date_from: a, date_to: b, days },
      comparison: { date_from: prevFrom, date_to: prevTo, days, available,
        reason: available ? null : `The previous period of equal length starts before the first available data${cov.min_date ? ` (${cov.min_date}).` : "."}` },
      metrics: compare(cur, prev),
      coverage: cov,
    };
  }

  function timeseries(p: Params) {
    const a = need(p, "date_from"), b = need(p, "date_to");
    const grain = String(p.grain ?? "day");
    const bucketOf = grain === "week" ? weekStart : grain === "month" ? monthStart : (s: string) => s;
    const buckets = new Map<string, { s: number; o: Set<number>; c: number; r: number }>();
    for (let n = toDay(a); n <= toDay(b); n++) {
      const k = bucketOf(fromDay(n));
      if (!buckets.has(k)) buckets.set(k, { s: 0, o: new Set(), c: 0, r: 0 });
    }
    for (const l of lines) {
      const d = orders[l[0]][2];
      if (d < a || d > b) continue;
      const bk = buckets.get(bucketOf(d))!;
      if (!cancelled(l[0])) { bk.s += l[8]; bk.o.add(l[0]); }
    }
    for (const t of txns) {
      if (t[7] < a || t[7] > b || t[3] !== "succeeded") continue;
      const bk = buckets.get(bucketOf(t[7]))!;
      if (t[2] === "payment") bk.c += t[4];
      if (t[2] === "refund") bk.r += t[4];
    }
    const points = [...buckets.entries()].sort(([x], [y]) => (x < y ? -1 : 1)).map(([bucket, v]) => ({
      bucket, active_ordered_value: m2(v.s), orders: v.o.size, successful_collections: m2(v.c),
      successful_refunds: m2(v.r), net_collections: m2(v.c - v.r),
    }));
    return { grain, date_from: a, date_to: b, points };
  }

  function topProducts(p: Params) {
    const a = need(p, "date_from"), b = need(p, "date_to");
    const cat = p.category ? String(p.category) : null;
    const limit = Math.min(50, Math.max(1, Number(p.limit ?? 10)));
    const per = new Map<number, { u: number; o: Set<number>; v: number }>();
    const cats = new Map<string, { u: number; o: Set<number>; v: number }>();
    for (const l of lines) {
      const d = orders[l[0]][2];
      if (d < a || d > b || cancelled(l[0])) continue;
      const category = products[l[3]][2];
      const c = cats.get(category) ?? { u: 0, o: new Set(), v: 0 };
      c.u += l[4]; c.o.add(l[0]); c.v += l[8]; cats.set(category, c);
      if (cat !== null && category !== cat) continue;
      const x = per.get(l[3]) ?? { u: 0, o: new Set(), v: 0 };
      x.u += l[4]; x.o.add(l[0]); x.v += l[8]; per.set(l[3], x);
    }
    const total = [...per.values()].reduce((s, x) => s + x.v, 0);
    const rows = [...per.entries()]
      .sort(([pa, x], [pb, y]) => y.v - x.v || (products[pa][0] < products[pb][0] ? -1 : 1))
      .slice(0, limit)
      .map(([pi, x]) => ({ sku: products[pi][0], name: products[pi][1], category: products[pi][2], units: x.u, orders: x.o.size,
        active_ordered_value: m2(x.v), share_pct_of_all: share2(x.v, total) }));
    return {
      products: rows,
      categories: [...cats.entries()].sort(([ca, x], [cb, y]) => y.v - x.v || (ca < cb ? -1 : 1))
        .map(([category, x]) => ({ category, active_ordered_value: m2(x.v), units: x.u, orders: x.o.size })),
    };
  }

  function statusDistribution(p: Params) {
    const a = need(p, "date_from"), b = need(p, "date_to");
    const by = new Map<string, { n: number; v: number }>();
    orders.forEach((o, oi) => {
      if (o[2] < a || o[2] > b) return;
      const x = by.get(o[3]) ?? { n: 0, v: 0 };
      x.n += 1; x.v += agg[oi].net; by.set(o[3], x);
    });
    return { items: [...by.entries()].sort(([sa, x], [sb, y]) => y.n - x.n || (sa < sb ? -1 : 1))
      .map(([status, x]) => ({ status, orders: x.n, ordered_value: m2(x.v) })) };
  }

  // ---- sales explorer
  function collectionState(o: (typeof agg)[number]) {
    if (o.coll === 0) return "unpaid";
    if (o.coll - o.refunds <= 0) return "refunded";
    if (o.refunds > 0) return "partially_refunded";
    if (o.coll < o.net) return "partially_paid";
    return "paid";
  }

  /** Orders matching the explorer filters (same semantics as _order_filters). */
  function filterOrders(p: Params): number[] {
    const a = need(p, "date_from"), b = need(p, "date_to");
    const statuses = new Set(list(p.status));
    const cat = p.category ? String(p.category) : null;
    const sku = p.product_sku ? String(p.product_sku) : null;
    const region = p.region ? String(p.region) : null;
    const city = p.city ? String(p.city) : null;
    const oid = p.order_id ? String(p.order_id).replace("#", "") : null;
    if (oid !== null && !/^\d{1,10}$/.test(oid)) throw new StaticApiError(422, "VALIDATION_ERROR", "order_id: digits only.");
    const out: number[] = [];
    orders.forEach((o, oi) => {
      if (o[2] < a || o[2] > b) return;
      if (statuses.size && !statuses.has(o[3])) return;
      const c = customers[o[5]];
      if (region !== null && c[3] !== region) return;
      if (city !== null && c[2] !== city) return;
      if (oid !== null && !String(o[0]).startsWith(String(Number(oid)))) return;
      if (cat !== null && !linesBy[oi].some((li) => products[lines[li][3]][2] === cat)) return;
      if (sku !== null && !linesBy[oi].some((li) => products[lines[li][3]][0] === sku)) return;
      out.push(oi);
    });
    return out;
  }

  function orderRow(oi: number) {
    const o = orders[oi], c = customers[o[5]], x = agg[oi];
    return {
      order_id: o[0], order_placed_at_utc: o[1], order_date: o[2], status: o[3], channel: o[4],
      customer_code: c[0], customer_name: c[1], customer_region: c[3], customer_city: c[2],
      line_count: x.lineCount, units: x.units, gross_merchandise_value: m2(x.gmv), ordered_value: m2(x.net),
      successful_collections: m2(x.coll), successful_refunds: m2(x.refunds), net_collections: m2(x.coll - x.refunds),
      transactions: x.txnCount, collection_state: collectionState(x),
    };
  }

  const nameCollator = new Intl.Collator("en", { sensitivity: "base" });
  function sortOrders(ids: number[], sort: string, dir: string) {
    const s = dir === "asc" ? 1 : -1;
    const key: Record<string, (oi: number) => number | string> = {
      order_date: (oi) => orders[oi][1], order_id: (oi) => orders[oi][0], ordered_value: (oi) => agg[oi].net,
      net_collections: (oi) => agg[oi].coll - agg[oi].refunds, status: (oi) => orders[oi][3],
      customer: (oi) => customers[orders[oi][5]][1],
    };
    const k = key[sort] ?? key.order_date;
    return ids.sort((x, y) => {
      const a = k(x), b = k(y);
      const c = typeof a === "number" ? a - (b as number) : sort === "customer" ? nameCollator.compare(a, b as string) : a < b ? -1 : a > b ? 1 : 0;
      return c !== 0 ? s * c : s * (orders[x][0] - orders[y][0]); // deterministic tie-break on order id
    });
  }

  function listOrders(p: Params) {
    const page = Math.max(1, Number(p.page ?? 1));
    const size = Math.min(100, Math.max(1, Number(p.page_size ?? 25)));
    const ids = sortOrders(filterOrders(p), String(p.sort ?? "order_date"), String(p.direction ?? "desc"));
    let ordered = 0, active = 0, net = 0;
    for (const oi of ids) { ordered += agg[oi].net; net += agg[oi].coll - agg[oi].refunds; if (!cancelled(oi)) active += agg[oi].net; }
    return {
      items: ids.slice((page - 1) * size, page * size).map(orderRow), page, page_size: size, total: ids.length,
      totals: { orders: ids.length, ordered_value: m2(ordered), active_ordered_value: m2(active), net_collections: m2(net) },
    };
  }

  /** Orders for the Excel export: order grain, oldest first (like iter_order_rows). */
  function exportRows(p: Params, limit = 100_000) {
    return sortOrders(filterOrders(p), "order_date", "asc").slice(0, limit).map((oi) => {
      const r = orderRow(oi);
      return { ...r, discounts: m2(agg[oi].gmv - agg[oi].net) };
    });
  }

  function orderDetail(id: number) {
    const oi = byOrderId.get(id);
    if (oi === undefined) throw new StaticApiError(404, "NOT_FOUND", `Order ${id} is not in the warehouse.`);
    const o = orders[oi], c = customers[o[5]], x = agg[oi];
    const ls = linesBy[oi].map((li) => lines[li]).sort((a, b) => a[2] - b[2]).map((l) => ({
      order_item_id: l[1], line_number: l[2], sku: products[l[3]][0], product_name: products[l[3]][1], category: products[l[3]][2],
      quantity: l[4], unit_price: m2(l[5]), current_list_price: m2(products[l[3]][3]), discount_amount: m2(l[6]),
      gross_amount: m2(l[7]), net_amount: m2(l[8]),
    }));
    const ts = txnsBy[oi].map((ti) => txns[ti]).map((t) => ({
      transaction_id: t[1], transaction_type: t[2], status: t[3], amount: m2(t[4]), payment_method: t[5],
      transaction_at_utc: t[6], transaction_date: t[7], refers_to_transaction_id: t[8], source_file: t[9],
    }));
    return {
      order: { order_id: o[0], order_placed_at_utc: o[1], order_date: o[2], status: o[3], channel: o[4], customer_code: c[0],
        customer_name: c[1], customer_city: c[2], customer_region: c[3], updated_run_id: o[6] },
      lines: ls, transactions: ts,
      totals: { gross_merchandise_value: m2(x.gmv), discounts: m2(x.gmv - x.net), ordered_value: m2(x.net),
        successful_collections: m2(x.coll), successful_refunds: m2(x.refunds), net_collections: m2(x.coll - x.refunds),
        collection_state: collectionState(x) },
      quarantined_records: data.order_quarantine[String(id)] ?? [],
    };
  }

  // ---- snapshot-backed endpoints
  function freshness() {
    const f = structuredClone(snap.freshness);
    if (f.last_successful_run?.finished_at_utc) {
      f.last_successful_run.minutes_ago = Math.max(0, Math.floor((now() - Date.parse(f.last_successful_run.finished_at_utc)) / 60_000));
    }
    return f;
  }
  function runs(p: Params) {
    const page = Math.max(1, Number(p.page ?? 1)), size = Math.min(100, Math.max(1, Number(p.page_size ?? 20)));
    const all = (snap.runs as Json[]).filter((r) => !p.status || r.status === p.status);
    return { items: all.slice((page - 1) * size, page * size), page, page_size: size, total: all.length };
  }
  function quarantine(p: Params) {
    const page = Math.max(1, Number(p.page ?? 1)), size = Math.min(100, Math.max(1, Number(p.page_size ?? 25)));
    const q = p.q ? String(p.q).toLowerCase() : null;
    const all = (snap.quarantine.items as Json[]).filter((r) =>
      (!p.entity || r.entity === p.entity) && (!p.error_code || r.error_code === p.error_code) &&
      (!p.status || r.status === p.status) && (!p.run_id || r.run_id === Number(p.run_id)) &&
      (q === null || String(r.source_record_id).toLowerCase().includes(q)));
    return { items: all.slice((page - 1) * size, page * size), page, page_size: size, total: all.length,
      error_codes: snap.quarantine.error_codes };
  }
  const snapOr404 = (v: Json, what: string) => { if (v === undefined) throw new StaticApiError(404, "NOT_FOUND", `${what} does not exist.`); return v; };

  function get(path: string, p: Params = {}): Json {
    let m: RegExpMatchArray | null;
    switch (path) {
      case "/api/health": return snap.health;
      case "/api/filters": return snap.filters;
      case "/api/freshness": return freshness();
      case "/api/definitions": return snap.definitions[p.lang === "ar" ? "ar" : "en"];
      case "/api/overview": return overview(p);
      case "/api/timeseries": return timeseries(p);
      case "/api/products/top": return topProducts(p);
      case "/api/orders/status-distribution": return statusDistribution(p);
      case "/api/orders": return listOrders(p);
      case "/api/customers": return { items: [] };
      case "/api/dev/scenarios": return { enabled: false, items: [] };
      case "/api/pipeline/runs": return runs(p);
      case "/api/quality": return snap.quality;
      case "/api/quality/quarantine": return quarantine(p);
      case "/api/reconciliation": return snapOr404(snap.reconciliation[p.run_id ? String(p.run_id) : "latest"] ?? snap.reconciliation.latest, "Reconciliation");
    }
    if ((m = path.match(/^\/api\/orders\/(\d+)$/))) return orderDetail(Number(m[1]));
    if ((m = path.match(/^\/api\/pipeline\/runs\/(\d+)$/))) return snapOr404(snap.run_details[m[1]], `Run ${m[1]}`);
    if ((m = path.match(/^\/api\/quality\/quarantine\/(\d+)$/))) return snapOr404(snap.quarantine.details[m[1]], `Quarantine record ${m[1]}`);
    throw new StaticApiError(404, "NOT_FOUND", `${path} is not available in this demo.`);
  }

  return { get, exportRows };
}

export type StaticApi = ReturnType<typeof createStaticApi>;
