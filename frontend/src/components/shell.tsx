"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { LayoutDashboard, Menu, Network, Scale, ShieldCheck, Table2, Workflow, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import type { DictKey } from "@/lib/dict";
import { api, type Freshness } from "@/lib/api";
import { relativeMinutes } from "@/lib/format";
import { useI18n, useLabel, type Lang } from "@/lib/i18n";

const NAV: { href: string; key: DictKey; icon: typeof LayoutDashboard }[] = [
  { href: "/", key: "nav.overview", icon: LayoutDashboard },
  { href: "/sales", key: "nav.sales", icon: Table2 },
  { href: "/pipeline", key: "nav.pipeline", icon: Workflow },
  { href: "/quality", key: "nav.quality", icon: ShieldCheck },
  { href: "/reconciliation", key: "nav.reconciliation", icon: Scale },
  { href: "/architecture", key: "nav.architecture", icon: Network },
];

function FreshnessNote() {
  const { t } = useI18n();
  const label = useLabel();
  const { data, isError } = useQuery({ queryKey: ["freshness"], queryFn: () => api.get<Freshness>("/api/freshness"), refetchInterval: 20_000 });
  if (isError) return <p className="text-xs text-bad">{t("shell.apiDown")}</p>;
  if (!data) return <p className="text-xs text-faint">{t("shell.checking")}</p>;
  const ok = data.last_successful_run;
  return (
    <div className="text-xs leading-relaxed text-muted">
      {data.active_run ? (
        <p className="font-medium text-accent">{t("shell.runActive", { id: data.active_run.run_id, status: label("status", data.active_run.status) })}</p>
      ) : null}
      <p>{ok ? t("shell.refreshed", { when: relativeMinutes(ok.minutes_ago) }) : t("shell.neverRefreshed")}</p>
      {data.last_run?.status === "failed" && <p className="text-bad">{t("shell.lastFailed", { id: data.last_run.run_id })}</p>}
    </div>
  );
}

function LanguageSwitch() {
  const { lang, setLang, t } = useI18n();
  const options: { value: Lang; label: string; htmlLang: string }[] = [
    { value: "en", label: "English", htmlLang: "en" },
    { value: "ar", label: "العربية", htmlLang: "ar" },
  ];
  return (
    <div role="radiogroup" aria-label={t("shell.language")} className="grid grid-cols-2 rounded-md border border-line-strong p-0.5">
      {options.map((o) => (
        <button key={o.value} role="radio" aria-checked={lang === o.value} lang={o.htmlLang} onClick={() => setLang(o.value)}
          className={clsx("h-8 rounded text-[0.8rem] font-medium", lang === o.value ? "bg-ink text-white" : "text-muted hover:text-ink")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [path]);

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV.map(({ href, key, icon: Icon }) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link key={href} href={href} aria-current={active ? "page" : undefined}
            className={clsx("flex h-10 items-center gap-3 rounded-md px-3 text-sm font-medium",
              active ? "bg-accent-soft text-accent-strong" : "text-muted hover:bg-hover hover:text-ink")}>
            <Icon className="size-[18px] shrink-0" aria-hidden />
            {t(key)}
          </Link>
        );
      })}
    </nav>
  );

  const brand = (
    <Link href="/" className="flex items-center gap-2.5 px-3" dir="ltr">
      <svg viewBox="0 0 24 24" className="size-6" aria-hidden>
        <path d="M3 17c4 0 4-6 9-6s5 6 9 6" fill="none" stroke="var(--color-accent)" strokeWidth="2.4" strokeLinecap="round" />
        <path d="M3 11c4 0 4-6 9-6s5 6 9 6" fill="none" stroke="var(--color-collect)" strokeWidth="2.4" strokeLinecap="round" strokeDasharray="3 3" />
      </svg>
      <span className="text-[1.05rem] font-semibold tracking-[-0.01em]">SalesFlow</span>
    </Link>
  );

  const footer = (
    <div className="mt-auto space-y-3 border-t border-line px-3 pt-4">
      <LanguageSwitch />
      <FreshnessNote />
      <p className="rounded-md bg-warn-soft px-2.5 py-2 text-xs leading-snug text-warn">{t("shell.synthetic")}</p>
    </div>
  );

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[232px_1fr]">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:start-3 focus:top-3 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2">{t("shell.skip")}</a>
      <aside className="sticky top-0 hidden h-dvh flex-col gap-6 border-e border-line bg-surface py-5 lg:flex">
        {brand}
        <div className="px-2">{nav}</div>
        {footer}
      </aside>
      <div className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-line bg-surface px-3 lg:hidden">
        {brand}
        <button onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={open ? t("shell.closeMenu") : t("shell.openMenu")}
          className="inline-flex size-10 items-center justify-center rounded-md hover:bg-hover">
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </div>
      {open && (
        <div className="fixed inset-x-0 bottom-0 top-14 z-30 flex flex-col gap-6 overflow-y-auto bg-surface py-4 lg:hidden">
          <div className="px-2">{nav}</div>
          {footer}
        </div>
      )}
      <main id="main" className="min-w-0 px-4 pb-16 pt-6 sm:px-6 lg:px-10 lg:pt-8">
        <div className="mx-auto max-w-[1240px]">{children}</div>
      </main>
    </div>
  );
}
