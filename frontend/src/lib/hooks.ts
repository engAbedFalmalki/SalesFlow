"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo } from "react";
import { api, type Coverage, type Health, type RunDetail } from "./api";
import { useI18n } from "./i18n";
import { addDays } from "./dates";

// The router applies replace() asynchronously, so neither useSearchParams nor
// window.location reflect an update until it commits. Quick successive updates
// (clear filters, then sort) build on the latest *requested* URL instead.
let pending: { path: string; query: string; at: number } | null = null;

/** Read/write URL search params so filtered views are shareable and survive reloads. */
export function useUrlState() {
  const sp = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  useEffect(() => {
    if (pending && pending.path === path && pending.query === sp.toString()) pending = null;
  }, [sp, path]);
  const set = useCallback((patch: Record<string, string | string[] | null | undefined>) => {
    const fresh = pending && pending.path === path && Date.now() - pending.at < 2000;
    const next = new URLSearchParams(fresh ? pending!.query : sp.toString());
    for (const [k, v] of Object.entries(patch)) {
      next.delete(k);
      if (Array.isArray(v)) v.forEach((x) => next.append(k, x));
      else if (v !== null && v !== undefined && v !== "") next.set(k, v);
    }
    const q = next.toString();
    pending = { path, query: q, at: Date.now() };
    router.replace(q ? `${path}?${q}` : path, { scroll: false });
  }, [sp, router, path]);
  return { sp, set };
}

export type Place = { value: string; en: string; ar: string };
export type Filters = {
  categories: string[]; products: { sku: string; name: string; category: string; list_price: string; is_active: boolean }[];
  regions: Place[]; cities: (Place & { region: string })[]; statuses: string[]; coverage: Coverage;
};

export function useFilters() {
  return useQuery({ queryKey: ["filters"], queryFn: () => api.get<Filters>("/api/filters"), staleTime: 60_000 });
}

/** Display name of a region or city in the UI language (stored values are English). */
export function usePlaceName() {
  const { lang } = useI18n();
  const filters = useFilters();
  const names = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of [...(filters.data?.regions ?? []), ...(filters.data?.cities ?? [])]) m.set(p.value, p.ar);
    return m;
  }, [filters.data]);
  return useCallback((v: string | null | undefined) => (!v ? "" : lang === "ar" ? names.get(v) ?? v : v), [lang, names]);
}

/** Polls a pipeline run until it finishes, then refreshes every view that reads warehouse data. */
export function useRunWatch(runId: number | null | undefined) {
  const qc = useQueryClient();
  const run = useQuery({
    queryKey: ["run", runId], enabled: !!runId,
    queryFn: () => api.get<RunDetail>(`/api/pipeline/runs/${runId}`),
    refetchInterval: (q) => (["queued", "running"].includes(q.state.data?.status ?? "queued") ? 1500 : false),
  });
  const status = run.data?.status;
  useEffect(() => {
    if (status === "succeeded" || status === "failed")
      qc.invalidateQueries({ predicate: (q) => !["run", "customers"].includes(String(q.queryKey[0])) });
  }, [status, qc]);
  return run;
}

export function useHealth() {
  return useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/api/health"), staleTime: 60_000 });
}

/** Date range from the URL, defaulting to the last 30 days of available data. */
export function useDateRange(defaultDays = 30) {
  const { sp, set } = useUrlState();
  const filters = useFilters();
  const cov = filters.data?.coverage;
  const range = useMemo(() => {
    const max = cov?.max_date ?? null;
    const min = cov?.min_date ?? null;
    const from = sp.get("from") ?? (max ? (addDays(max, -(defaultDays - 1)) < (min ?? "") ? min! : addDays(max, -(defaultDays - 1))) : null);
    const to = sp.get("to") ?? max;
    return { from, to, min, max };
  }, [sp, cov, defaultDays]);
  const setRange = useCallback((from: string, to: string) => set({ from, to, page: null }), [set]);
  return { ...range, setRange, filters };
}
