"use client";

import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, PageHeader, Panel, Section, td, th } from "@/components/ui";
import { api } from "@/lib/api";
import { SCENARIOS_AR, type DictKey } from "@/lib/dict";
import { count } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

type Defs = {
  metrics: { key: string; name: string; unit: string; date_basis: string; definition: string; sql: string }[];
  grains: { table: string; grain: string; key: string; updates: string }[];
  date_rules: string[]; exclusions: string[]; limitations: string[];
  dataset: { notice: string; generator: Record<string, string | number>; baseline: { ops_tables: Record<string, number>; payment_files: number }; scenarios: { id: string; title: string; description: string }[] } | null;
};
type T = (k: DictKey, v?: Record<string, string | number>) => string;

function Box({ x, y, w, h, title, lines, tone = "base" }: { x: number; y: number; w: number; h: number; title: string; lines: string[]; tone?: "base" | "accent" | "warn" | "muted" }) {
  const stroke = { base: "var(--color-line-strong)", accent: "var(--color-accent)", warn: "var(--color-warn)", muted: "var(--color-line-strong)" }[tone];
  const fill = { base: "var(--color-surface)", accent: "var(--color-accent-soft)", warn: "var(--color-warn-soft)", muted: "var(--color-paper)" }[tone];
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={8} fill={fill} stroke={stroke} strokeWidth={1.25} />
      <text x={x + 12} y={y + 22} fontSize={13.5} fontWeight={600} fill="var(--color-ink)">{title}</text>
      {lines.map((l, i) => <text key={i} x={x + 12} y={y + 41 + i * 16} fontSize={11.5} fill="var(--color-muted)">{l}</text>)}
    </g>
  );
}

function Arrow({ d, label, lx, ly, dashed }: { d: string; label?: string; lx?: number; ly?: number; dashed?: boolean }) {
  return (
    <g>
      <path d={d} fill="none" stroke="var(--color-faint)" strokeWidth={1.5} markerEnd="url(#arrow)" strokeDasharray={dashed ? "4 4" : undefined} />
      {label && <text x={lx} y={ly} fontSize={10.5} fill="var(--color-muted)" textAnchor="middle">{label}</text>}
    </g>
  );
}

function Diagram({ t }: { t: T }) {
  const L = (...keys: DictKey[]) => keys.map((k) => t(k));
  return (
    <div className="overflow-x-auto" dir="ltr" data-lenis-prevent>
      <svg viewBox="0 0 1120 440" className="min-w-[900px]" role="img" aria-labelledby="arch-title arch-desc">
        <title id="arch-title">{t("a.svgTitle")}</title>
        <desc id="arch-desc">{t("a.svgDesc")}</desc>
        <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="var(--color-faint)" /></marker></defs>

        <text x={20} y={22} fontSize={12} fill="var(--color-faint)">{t("svg.sources")}</text>
        <Box x={20} y={34} w={200} h={84} title={t("svg.ops")} lines={L("svg.ops1", "svg.ops2", "svg.ops3")} />
        <Box x={20} y={128} w={200} h={78} title={t("svg.csv")} lines={L("svg.csv1", "svg.csv2")} />
        <Box x={20} y={222} w={200} h={78} title={t("svg.json")} lines={L("svg.json1", "svg.json2")} />

        <Box x={292} y={100} w={170} h={128} title={t("svg.raw")} lines={L("svg.raw1", "svg.raw2", "svg.raw3", "svg.raw4")} />
        <Arrow d="M220 76 C255 76 255 140 290 140" />
        <Arrow d="M220 167 L290 167" />
        <Arrow d="M220 261 C255 261 255 195 290 195" />

        <Box x={534} y={52} w={180} h={110} title={t("svg.stg")} lines={L("svg.stg1", "svg.stg2", "svg.stg3", "svg.stg4")} tone="muted" />
        <Box x={534} y={186} w={180} h={86} title={t("svg.q")} lines={L("svg.q1", "svg.q2", "svg.q3")} tone="warn" />
        <Arrow d="M462 150 C500 150 500 110 532 110" label={t("svg.valid")} lx={498} ly={120} />
        <Arrow d="M462 190 C500 190 500 228 532 228" label={t("svg.invalid")} lx={498} ly={222} />
        <Arrow d="M624 186 L624 164" dashed />
        <text x={632} y={180} fontSize={10.5} fill="var(--color-muted)">{t("svg.reprocess")}</text>

        <Box x={786} y={34} w={190} h={238} title={t("svg.dw")} tone="accent"
          lines={[...L("svg.dw1", "svg.dw2", "svg.dw3", "svg.dw4"), "", ...L("svg.dw5", "svg.dw6", "svg.dw7", "svg.dw8")]} />
        <Arrow d="M714 107 L784 107" label={t("svg.merge")} lx={749} ly={99} />

        <Box x={1010} y={70} w={100} h={62} title={t("svg.api")} lines={L("svg.api1")} />
        <Box x={1010} y={170} w={100} h={62} title={t("svg.web")} lines={L("svg.web1")} />
        <Arrow d="M976 101 L1008 101" />
        <Arrow d="M1060 132 L1060 168" />

        <rect x={292} y={318} width={684} height={100} rx={8} fill="var(--color-paper)" stroke="var(--color-line-strong)" strokeDasharray="5 4" />
        <text x={306} y={340} fontSize={13.5} fontWeight={600} fill="var(--color-ink)">{t("svg.ctl")}</text>
        {L("svg.ctl1", "svg.ctl2", "svg.ctl3", "svg.ctl4").map((s, i) => <text key={s} x={306} y={360 + i * 17} fontSize={11.5} fill="var(--color-muted)">{s}</text>)}
        {L("svg.ctl5", "svg.ctl6", "svg.ctl7").map((s, i) => <text key={s} x={660} y={360 + i * 17} fontSize={11.5} fill="var(--color-muted)">{s}</text>)}

        <Box x={20} y={330} w={200} h={78} title={t("svg.worker")} lines={L("svg.worker1", "svg.worker2")} />
        <Arrow d="M220 369 L290 369" />
      </svg>
    </div>
  );
}

export default function ArchitecturePage() {
  const { t, lang } = useI18n();
  const q = useQuery({ queryKey: ["defs", lang], queryFn: () => api.get<Defs>("/api/definitions", { lang }), staleTime: Infinity });
  const d = q.data;
  return (
    <>
      <PageHeader title={t("a.title")} description={t("a.desc")} />
      <Panel className="p-4"><Diagram t={t} /></Panel>

      {q.isError ? <div className="mt-6"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div> : !d ? <Loading /> : (
        <>
          <Section title={t("a.sources")}>
            <div className="grid gap-4 md:grid-cols-3">
              {([["a.src.ops", "a.src.ops.body"], ["a.src.csv", "a.src.csv.body"], ["a.src.json", "a.src.json.body"]] as [DictKey, DictKey][]).map(([title, body]) => (
                <div key={title} className="rounded-lg border border-line bg-surface p-4"><h3 className="font-semibold">{t(title)}</h3><p className="mt-1 text-sm leading-relaxed text-muted">{t(body)}</p></div>
              ))}
            </div>
          </Section>

          <Section title={t("a.metrics")} description={t("a.metricsDesc")}>
            <Panel className="overflow-x-auto">
              <table className="w-full min-w-[760px]">
                <thead className="border-b border-line"><tr><th className={th}>{t("a.col.metric")}</th><th className={th}>{t("a.col.basis")}</th><th className={th}>{t("a.col.definition")}</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {d.metrics.map((m) => (
                    <tr key={m.key}>
                      <td className={`${td} font-medium`}>{m.name}</td>
                      <td className={`${td} whitespace-nowrap`}><span className={m.date_basis === "order date" ? "mark-order" : "mark-txn"} /> <span className="ms-1">{m.date_basis === "order date" ? t("a.basis.order") : t("a.basis.txn")}</span></td>
                      <td className={td}>{m.definition}<p className="mt-1 text-xs text-faint" dir="ltr">{m.sql}</p></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          </Section>

          <div className="grid gap-x-8 lg:grid-cols-2">
            <Section title={t("a.dates")}>
              <ul className="list-disc space-y-1.5 ps-5 text-sm leading-relaxed">{d.date_rules.map((r) => <li key={r}>{r}</li>)}</ul>
            </Section>
            <Section title={t("a.scope")}>
              <ul className="list-disc space-y-1.5 ps-5 text-sm leading-relaxed">{d.exclusions.map((r) => <li key={r}>{r}</li>)}</ul>
            </Section>
          </div>

          <Section title={t("a.tables")} description={t("a.tablesDesc")}>
            <Panel className="overflow-x-auto">
              <table className="w-full min-w-[760px]">
                <thead className="border-b border-line"><tr><th className={th}>{t("a.col.table")}</th><th className={th}>{t("a.col.grain")}</th><th className={th}>{t("a.col.keys")}</th><th className={th}>{t("a.col.updates")}</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {d.grains.map((g) => (
                    <tr key={g.table}><td className={`${td} whitespace-nowrap font-medium`} dir="ltr">{g.table}</td><td className={td}>{g.grain}</td><td className={`${td} text-muted`} dir="ltr">{g.key}</td><td className={td}>{g.updates}</td></tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          </Section>

          {d.dataset && (
            <Section title={t("a.dataset")}>
              <Panel className="p-5">
                <p className="max-w-3xl text-sm leading-relaxed">{d.dataset.notice}</p>
                <dl className="num mt-4 grid gap-x-8 gap-y-1 text-sm sm:grid-cols-3">
                  {([[t("a.seed"), d.dataset.generator.seed], [t("a.periodLabel"), `${d.dataset.generator.start_date} → ${d.dataset.generator.end_date}`],
                    [t("a.customers"), count(d.dataset.baseline.ops_tables.customers)], [t("a.orders"), count(d.dataset.baseline.ops_tables.orders)],
                    [t("a.lines"), count(d.dataset.baseline.ops_tables.order_items)], [t("a.files"), count(d.dataset.baseline.payment_files)]] as [string, string | number][]).map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-4 border-b border-line py-1.5"><dt className="text-muted">{k}</dt><dd dir="ltr">{v}</dd></div>
                  ))}
                </dl>
                <h3 className="mt-5 font-semibold">{t("a.scenarios")}</h3>
                <ol className="mt-2 grid gap-x-8 gap-y-2 text-sm md:grid-cols-2">
                  {d.dataset.scenarios.map((s, i) => {
                    const text = lang === "ar" && SCENARIOS_AR[s.id] ? SCENARIOS_AR[s.id] : s;
                    return <li key={s.id}><span className="num text-faint">{i + 1}.</span> <b className="font-medium">{text.title}.</b> <span className="text-muted">{text.description}</span></li>;
                  })}
                </ol>
              </Panel>
            </Section>
          )}

          <Section title={t("a.limitations")}>
            <ul className="list-disc space-y-1.5 ps-5 text-sm leading-relaxed">{d.limitations.map((r) => <li key={r}>{r}</li>)}</ul>
          </Section>
        </>
      )}
    </>
  );
}
