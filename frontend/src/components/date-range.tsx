"use client";

import clsx from "clsx";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { addDays, daysBetween, PRESETS, presetRange } from "@/lib/dates";
import type { DictKey } from "@/lib/dict";
import { bizDate, latinDigits, monthYear } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { inputClass } from "./ui";

const ISO = /^\d{4}-\d{2}-\d{2}$/;

function validIso(s: string): boolean {
  if (!ISO.test(s)) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

/**
 * Date input that always shows Latin digits (the browser's native date input
 * follows the OS locale, which can render Arabic-Indic digits). Type YYYY-MM-DD
 * or pick from the calendar. Values are Asia/Riyadh business dates.
 */
export function DateField({ label, value, min, max, onChange }: {
  label: string; value: string; min: string; max: string; onChange: (v: string) => void;
}) {
  const { t, lang } = useI18n();
  const id = useId();
  const [text, setText] = useState(value);
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(value.slice(0, 7) + "-01");
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => { setText(value); setMonth(value.slice(0, 7) + "-01"); }, [value]);

  const error = text && !validIso(text) ? t("date.format") : text && (text < min || text > max) ? t("date.outside", { min, max }) : null;
  const commit = () => { if (!error && text && text !== value) onChange(text); };

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!boxRef.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const weekdays = useMemo(() => {
    const f = new Intl.DateTimeFormat(lang === "ar" ? "ar-u-nu-latn" : "en-GB", { weekday: "narrow", timeZone: "UTC" });
    return Array.from({ length: 7 }, (_, i) => f.format(new Date(Date.UTC(2026, 0, 4 + i)))); // 4 Jan 2026 is a Sunday
  }, [lang]);
  const days = useMemo(() => {
    const first = new Date(`${month}T00:00:00Z`);
    const start = addDays(month, -first.getUTCDay());
    return Array.from({ length: 42 }, (_, i) => addDays(start, i));
  }, [month]);
  const shiftMonth = (n: number) => {
    const d = new Date(`${month}T00:00:00Z`);
    d.setUTCMonth(d.getUTCMonth() + n);
    setMonth(d.toISOString().slice(0, 10));
  };

  return (
    <div ref={boxRef} className="relative flex flex-col gap-1">
      <label htmlFor={id} className="text-[0.8rem] font-medium text-muted">{label}</label>
      <div className="flex">
        <input id={id} dir="ltr" inputMode="numeric" autoComplete="off" placeholder="YYYY-MM-DD" value={text} maxLength={10}
          onChange={(e) => setText(latinDigits(e.target.value).replace(/[^\d-]/g, ""))} onBlur={commit}
          onKeyDown={(e) => { if (e.key === "Enter") commit(); }}
          aria-invalid={!!error} aria-describedby={error ? `${id}-err` : undefined}
          className={clsx(inputClass, "num w-[8.5rem] rounded-e-none text-center", error && "border-bad")} />
        <button type="button" onClick={() => setOpen((o) => !o)} aria-label={t("date.openCalendar")} aria-expanded={open}
          className="-ms-px inline-flex h-10 w-10 items-center justify-center rounded-e-md border border-line-strong bg-surface hover:bg-hover">
          <CalendarDays className="size-4 text-muted" aria-hidden />
        </button>
      </div>
      {error && <p id={`${id}-err`} className="text-xs text-bad">{error}</p>}
      {open && (
        <div role="dialog" aria-label={label} data-lenis-prevent
          className="absolute top-full z-40 mt-1 w-72 rounded-lg border border-line bg-surface p-3 shadow-xl [inset-inline-start:0]">
          <div className="mb-2 flex items-center justify-between">
            <button type="button" onClick={() => shiftMonth(-1)} aria-label={t("date.prevMonth")} className="inline-flex size-8 items-center justify-center rounded hover:bg-hover">
              <ChevronLeft className="size-4 rtl:rotate-180" aria-hidden />
            </button>
            <span className="num text-sm font-medium" aria-live="polite">{monthYear(month)}</span>
            <button type="button" onClick={() => shiftMonth(1)} aria-label={t("date.nextMonth")} className="inline-flex size-8 items-center justify-center rounded hover:bg-hover">
              <ChevronRight className="size-4 rtl:rotate-180" aria-hidden />
            </button>
          </div>
          <div className="grid grid-cols-7 gap-0.5 text-center text-[0.7rem] text-faint" aria-hidden>
            {weekdays.map((w, i) => <span key={i} className="py-1">{w}</span>)}
          </div>
          <div className="num grid grid-cols-7 gap-0.5">
            {days.map((d) => {
              const inMonth = d.slice(0, 7) === month.slice(0, 7);
              const disabled = d < min || d > max;
              const selected = d === value;
              return (
                <button key={d} type="button" disabled={disabled} aria-pressed={selected} aria-label={bizDate(d)}
                  onClick={() => { setOpen(false); onChange(d); }}
                  className={clsx("h-8 rounded text-[0.8rem]", selected ? "bg-accent font-medium text-white" : "hover:bg-hover",
                    !inMonth && !selected && "text-faint", disabled && "cursor-not-allowed opacity-30 hover:bg-transparent")}>
                  {Number(d.slice(8, 10))}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export function DateRangeControl({ from, to, min, max, onChange }: {
  from: string; to: string; min: string; max: string; onChange: (from: string, to: string) => void;
}) {
  const { t } = useI18n();
  const active = PRESETS.find((p) => { const r = presetRange(p, min, max); return r.from === from && r.to === to; })?.key ?? "custom";
  const invalid = from > to;
  return (
    <div className="flex flex-wrap items-start gap-2">
      <label className="flex flex-col gap-1 text-[0.8rem] font-medium text-muted">
        {t("date.period")}
        <select aria-label={t("date.presetLabel")} className={`${inputClass} pe-8`} value={active}
          onChange={(e) => { const p = PRESETS.find((x) => x.key === e.target.value); if (p) { const r = presetRange(p, min, max); onChange(r.from, r.to); } }}>
          {PRESETS.map((p) => <option key={p.key} value={p.key}>{t(`date.preset.${p.key}` as DictKey)}</option>)}
          <option value="custom" disabled>{t("date.custom")}</option>
        </select>
      </label>
      <DateField label={t("date.from")} value={from} min={min} max={max} onChange={(v) => onChange(v, v > to ? v : to)} />
      <DateField label={t("date.to")} value={to} min={min} max={max} onChange={(v) => onChange(v < from ? v : from, v)} />
      <p className="basis-full text-xs text-muted" aria-live="polite">
        {invalid ? <span className="text-bad">{t("date.invalid")}</span>
          : t("date.summary", { days: daysBetween(from, to), min: bizDate(min), max: bizDate(max) })}
      </p>
    </div>
  );
}

export { addDays };
