"use client";

import * as Dialog from "@radix-ui/react-dialog";
import * as Tip from "@radix-ui/react-tooltip";
import clsx from "clsx";
import { ChevronLeft, ChevronRight, CircleAlert, Info, LoaderCircle, X } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { count } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { HeroRays, HeroTitle, PageTitle, Reveal } from "./motion";

// ---------------------------------------------------------------- layout

/** Page title row. `hero` (home page) adds the blur-in title and the light-ray backdrop. */
export function PageHeader({ title, description, actions, hero }: { title: string; description?: ReactNode; actions?: ReactNode; hero?: boolean }) {
  return (
    <header className={clsx("mb-6 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between", hero && "relative isolate pb-2")}>
      {hero && <HeroRays />}
      <div className="max-w-2xl">
        {hero ? <HeroTitle text={title} /> : <PageTitle text={title} />}
        {description && <div className="mt-1.5 text-[0.95rem] leading-relaxed text-muted">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Section({ title, description, actions, children, className }: {
  title: string; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={clsx("mt-8", className)} aria-label={title}>
      <Reveal>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 className="text-lg font-semibold">{title}</h2>
            {description && <div className="mt-0.5 text-sm text-muted">{description}</div>}
          </div>
          {actions}
        </div>
        {children}
      </Reveal>
    </section>
  );
}

export function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx("rounded-lg border border-line bg-surface", className)}>{children}</div>;
}

// ---------------------------------------------------------------- controls

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; busy?: boolean; size?: "md" | "sm" };

export function Button({ variant = "secondary", busy, size = "md", className, children, disabled, ...rest }: BtnProps) {
  return (
    <button
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        size === "md" ? "h-10 px-4 text-sm" : "h-8 px-3 text-[0.8rem]",
        variant === "primary" && "bg-accent text-white hover:bg-accent-strong",
        variant === "secondary" && "border border-line-strong bg-surface text-ink hover:bg-hover",
        variant === "ghost" && "text-muted hover:bg-hover hover:text-ink",
        variant === "danger" && "border border-bad/40 bg-surface text-bad hover:bg-bad-soft",
        className,
      )}
    >
      {busy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function Field({ label, children, htmlFor }: { label: string; children: ReactNode; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className="flex flex-col gap-1 text-[0.8rem] font-medium text-muted">
      {label}
      {children}
    </label>
  );
}

export const inputClass =
  "h-10 rounded-md border border-line-strong bg-surface px-3 text-sm text-ink placeholder:text-faint hover:border-faint";

export function Select({ value, onChange, options, id, ariaLabel, className }: {
  value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; id?: string; ariaLabel?: string; className?: string;
}) {
  return (
    <select id={id} aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)} className={clsx(inputClass, "pe-8", className)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

export function Segmented<T extends string>({ value, onChange, options, label }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: string }[]; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-md border border-line-strong bg-surface p-0.5">
      {options.map((o) => (
        <button key={o.value} role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)}
          className={clsx("h-8 rounded px-3 text-[0.8rem] font-medium", value === o.value ? "bg-ink text-white" : "text-muted hover:text-ink")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function ToggleChip({ pressed, onClick, children }: { pressed: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" aria-pressed={pressed} onClick={onClick}
      className={clsx("h-8 rounded-full border px-3 text-[0.8rem] font-medium",
        pressed ? "border-accent bg-accent-soft text-accent-strong" : "border-line-strong bg-surface text-muted hover:text-ink")}>
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- status

const TONES = {
  ok: "bg-ok-soft text-ok", warn: "bg-warn-soft text-warn", bad: "bg-bad-soft text-bad",
  info: "bg-info-soft text-info", neutral: "bg-hover text-muted", accent: "bg-accent-soft text-accent-strong",
} as const;
export type Tone = keyof typeof TONES;

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return <span title={title} className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium", TONES[tone])}>{children}</span>;
}

export const RUN_TONE: Record<string, Tone> = { queued: "info", running: "accent", succeeded: "ok", failed: "bad", pending: "neutral", skipped: "neutral" };
export const ORDER_TONE: Record<string, Tone> = { delivered: "ok", shipped: "info", processing: "accent", pending: "warn", cancelled: "bad" };
export const RECON_TONE: Record<string, Tone> = { matched: "ok", explained: "warn", unresolved: "bad", informational: "info" };
export const COLLECTION_TONE: Record<string, Tone> = {
  paid: "ok", unpaid: "warn", partially_refunded: "info", refunded: "neutral", partially_paid: "warn",
};

// ---------------------------------------------------------------- feedback states

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={clsx("animate-pulse rounded bg-hover", className)} />;
}

export function Loading({ label }: { label?: string }) {
  const { t } = useI18n();
  return (
    <div role="status" className="flex items-center gap-2 px-4 py-10 text-sm text-muted">
      <LoaderCircle className="size-4 animate-spin" aria-hidden /> {label ?? t("common.loading")}…
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error as ApiError;
  const { t, lang } = useI18n();
  const ar = lang === "ar" ? (e?.details as { message_ar?: string } | undefined)?.message_ar : undefined;
  return (
    <div role="alert" className="flex flex-col items-start gap-2 rounded-lg border border-bad/30 bg-bad-soft px-4 py-4 text-sm">
      <div className="flex items-center gap-2 font-medium text-bad"><CircleAlert className="size-4" aria-hidden />
        {e?.code === "NETWORK" ? t("common.apiDown") : t("common.loadFailed")}</div>
      <p className="text-ink">{ar ?? e?.message ?? String(error)}</p>
      {onRetry && <Button size="sm" onClick={onRetry}>{t("common.tryAgain")}</Button>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-4 py-10 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-md text-sm text-muted">{children}</div>}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: Tone; children: ReactNode }) {
  return <div role="status" className={clsx("rounded-md px-3 py-2 text-sm", TONES[tone])}>{children}</div>;
}

// ---------------------------------------------------------------- tooltips

export function InfoTip({ children, label }: { children: ReactNode; label?: string }) {
  const { t } = useI18n();
  label = label ?? t("common.moreInfo");
  return (
    <Tip.Root delayDuration={150}>
      <Tip.Trigger asChild>
        <button type="button" aria-label={label} className="inline-flex size-5 items-center justify-center rounded-full text-faint hover:text-ink">
          <Info className="size-3.5" aria-hidden />
        </button>
      </Tip.Trigger>
      <Tip.Portal>
        <Tip.Content sideOffset={6} className="z-50 max-w-xs rounded-md bg-ink px-3 py-2 text-xs leading-relaxed text-white shadow-lg">
          {children}
          <Tip.Arrow className="fill-ink" />
        </Tip.Content>
      </Tip.Portal>
    </Tip.Root>
  );
}

// ---------------------------------------------------------------- pagination & tables

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  const { t } = useI18n();
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-3 border-t border-line px-4 py-3 text-sm text-muted">
      <span className="num">{t("common.rangeOf", { from: count(from), to: count(to), total: count(total) })}</span>
      <div className="flex items-center gap-1">
        <Button size="sm" variant="ghost" onClick={() => onPage(page - 1)} disabled={page <= 1} aria-label={t("common.previous")}><ChevronLeft className="size-4 rtl:rotate-180" /></Button>
        <span className="num px-2">{t("common.pageOf", { page, pages })}</span>
        <Button size="sm" variant="ghost" onClick={() => onPage(page + 1)} disabled={page >= pages} aria-label={t("common.next")}><ChevronRight className="size-4 rtl:rotate-180" /></Button>
      </div>
    </nav>
  );
}

export const th = "px-4 py-2.5 text-start text-[0.78rem] font-medium text-muted whitespace-nowrap";
export const td = "px-4 py-2.5 text-sm align-top";

// ---------------------------------------------------------------- drawer

export function Drawer({ open, onClose, title, description, children }: {
  open: boolean; onClose: () => void; title: string; description?: ReactNode; children: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/30" />
        {/* Without a description, opt out explicitly instead of repeating the title to screen readers. */}
        <Dialog.Content {...(description ? {} : { "aria-describedby": undefined })}
          className="fixed inset-y-0 end-0 z-50 flex w-full max-w-3xl flex-col bg-paper shadow-2xl focus:outline-none">
          <div className="flex items-start justify-between gap-4 border-b border-line bg-surface px-6 py-4">
            <div>
              <Dialog.Title className="text-lg font-semibold">{title}</Dialog.Title>
              {description && <Dialog.Description asChild><div className="mt-0.5 text-sm text-muted">{description}</div></Dialog.Description>}
            </div>
            <Dialog.Close asChild>
              <CloseButton />
            </Dialog.Close>
          </div>
          <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function ConfirmDialog({ open, title, children, confirmLabel, onConfirm, onCancel, busy }: {
  open: boolean; title: string; children: ReactNode; confirmLabel: string; onConfirm: () => void; onCancel: () => void; busy?: boolean;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/30" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg bg-surface p-6 shadow-2xl">
          <Dialog.Title className="text-lg font-semibold">{title}</Dialog.Title>
          <Dialog.Description asChild><div className="mt-2 text-sm leading-relaxed text-muted">{children}</div></Dialog.Description>
          <div className="mt-6 flex justify-end gap-2">
            <CancelButton onClick={onCancel} />
            <Button variant="primary" onClick={onConfirm} busy={busy}>{confirmLabel}</Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="num min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function CloseButton(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  const { t } = useI18n();
  return <Button {...props} variant="ghost" size="sm" aria-label={t("common.close")}><X className="size-4" /></Button>;
}

function CancelButton({ onClick }: { onClick: () => void }) {
  const { t } = useI18n();
  return <Button onClick={onClick}>{t("common.cancel")}</Button>;
}
