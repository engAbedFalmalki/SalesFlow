// Display formatting. Money stays a decimal string; we only group digits, so
// displayed amounts are exactly what the warehouse returned. Digits are always
// Latin (0-9), in English and in Arabic.

type Lang = "en" | "ar";
let LANG: Lang = "en";
export function setFormatLang(l: Lang) { LANG = l; }

const TZ = "Asia/Riyadh";
// Arabic month names, Gregorian calendar, Latin digits.
const LOCALE: Record<Lang, string> = { en: "en-GB", ar: "ar-u-nu-latn-ca-gregory" };

export function groupDigits(intPart: string): string {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function withCurrency(body: string, sign: string, currency: boolean): string {
  if (!currency) return `${sign}${body}`;
  return LANG === "ar" ? `${sign}${body} ر.س` : `${sign}SAR ${body}`;
}

export function money(v: string | number | null | undefined, opts: { sign?: boolean; currency?: boolean } = {}): string {
  if (v === null || v === undefined || v === "") return LANG === "ar" ? "غير متاح" : "n/a";
  const s = String(v);
  const neg = s.startsWith("-");
  const [i, f = "00"] = (neg ? s.slice(1) : s).split(".");
  const body = `${groupDigits(i)}.${f.padEnd(2, "0").slice(0, 2)}`;
  const sign = neg ? "−" : opts.sign ? "+" : "";
  return withCurrency(body, sign, opts.currency !== false);
}

/** Compact money for large KPI figures (display only; full value goes in the title). */
export function moneyCompact(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return LANG === "ar" ? "غير متاح" : "n/a";
  const n = Number(v);
  const abs = Math.abs(n);
  const sign = n < 0 ? "−" : "";
  if (abs >= 1_000_000) return withCurrency(`${(abs / 1_000_000).toFixed(2)}M`, sign, true);
  if (abs >= 10_000) return withCurrency(`${(abs / 1_000).toFixed(1)}K`, sign, true);
  return money(v);
}

export function count(v: number | string | null | undefined): string {
  if (v === null || v === undefined) return LANG === "ar" ? "غير متاح" : "n/a";
  const n = Number(v);
  return n < 0 ? `−${groupDigits(String(-n))}` : groupDigits(String(n));
}

const fmtCache = new Map<string, Intl.DateTimeFormat>();
function fmt(kind: "dt" | "d" | "ds" | "my"): Intl.DateTimeFormat {
  const key = `${LANG}:${kind}`;
  let f = fmtCache.get(key);
  if (!f) {
    const opts: Intl.DateTimeFormatOptions =
      kind === "dt" ? { timeZone: TZ, day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }
        : kind === "d" ? { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" }
          : kind === "ds" ? { timeZone: "UTC", day: "numeric", month: "short" }
            : { timeZone: "UTC", month: "long", year: "numeric" };
    f = new Intl.DateTimeFormat(LOCALE[LANG], opts);
    fmtCache.set(key, f);
  }
  return f;
}

/** UTC ISO timestamp -> Riyadh local date-time. */
export function dateTime(iso: string | null | undefined): string {
  return iso ? fmt("dt").format(new Date(iso)) : (LANG === "ar" ? "غير متاح" : "n/a");
}

/** Business date (yyyy-mm-dd, already Riyadh) -> readable date. */
export function bizDate(d: string | null | undefined, short = false): string {
  if (!d) return LANG === "ar" ? "غير متاح" : "n/a";
  return fmt(short ? "ds" : "d").format(new Date(`${d}T00:00:00Z`));
}

export function monthYear(d: string): string {
  return fmt("my").format(new Date(`${d}T00:00:00Z`));
}

export function relativeMinutes(mins: number | null | undefined): string {
  const ar = LANG === "ar";
  if (mins === null || mins === undefined) return ar ? "أبداً" : "never";
  if (mins < 1) return ar ? "الآن" : "just now";
  if (mins < 60) return ar ? `قبل ${mins} دقيقة` : `${mins} min ago`;
  if (mins < 60 * 24) return ar ? `قبل ${Math.floor(mins / 60)} ساعة` : `${Math.floor(mins / 60)} h ago`;
  return ar ? `قبل ${Math.floor(mins / 1440)} يوم` : `${Math.floor(mins / 1440)} days ago`;
}

export function seconds(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return LANG === "ar" ? "غير متاح" : "n/a";
  const s = Number(v);
  if (LANG === "ar") return s < 60 ? `${s.toFixed(1)} ث` : `${Math.floor(s / 60)} د ${Math.round(s % 60)} ث`;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function humanize(code: string): string {
  const s = code.replace(/_/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Typed input from an Arabic or Persian keyboard: turn its digits into Latin 0-9. */
export function latinDigits(s: string): string {
  return s.replace(/[٠-٩۰-۹]/g, (c) => String((c.charCodeAt(0) & 0xf) % 10))
    .replace(/٫/g, "."); // Arabic decimal separator
}
