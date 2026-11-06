"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { ar, CHECK_NAMES, en, ERROR_TITLES, type DictKey } from "./dict";
import { setFormatLang } from "./format";

import { LANG_COOKIE, type Lang } from "./lang";
export { LANG_COOKIE, type Lang };

type Vars = Record<string, string | number>;
type Ctx = { lang: Lang; dir: "ltr" | "rtl"; setLang: (l: Lang) => void; t: (k: DictKey, v?: Vars) => string };

const I18n = createContext<Ctx | null>(null);

export function translate(lang: Lang, key: DictKey, vars?: Vars): string {
  let s: string = (lang === "ar" ? ar : en)[key] ?? en[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  return s;
}

export function LanguageProvider({ initial, children }: { initial: Lang; children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initial);
  setFormatLang(lang); // formatting helpers read the active language synchronously
  const setLang = useCallback((l: Lang) => {
    document.cookie = `${LANG_COOKIE}=${l}; path=/; max-age=31536000; samesite=lax`;
    document.documentElement.lang = l;
    document.documentElement.dir = l === "ar" ? "rtl" : "ltr";
    setFormatLang(l);
    setLangState(l);
  }, []);
  const value = useMemo<Ctx>(() => ({
    lang, dir: lang === "ar" ? "rtl" : "ltr", setLang, t: (k, v) => translate(lang, k, v),
  }), [lang, setLang]);
  return <I18n.Provider value={value}>{children}</I18n.Provider>;
}

export function useI18n(): Ctx {
  const c = useContext(I18n);
  if (!c) throw new Error("useI18n outside LanguageProvider");
  return c;
}

/** Translate a dynamic key such as a status, falling back to a humanised code. */
export function useLabel() {
  const { lang } = useI18n();
  return useCallback((prefix: string, code: string | null | undefined): string => {
    if (!code) return "";
    const key = `${prefix}.${code}` as DictKey;
    if (key in en) return translate(lang, key);
    const s = code.replace(/_/g, " ").toLowerCase();
    return s.charAt(0).toUpperCase() + s.slice(1);
  }, [lang]);
}

export function checkName(code: string, lang: Lang, fallback: string): string {
  return CHECK_NAMES[code]?.[lang] ?? fallback;
}

export function errorTitle(code: string, lang: Lang): string {
  return ERROR_TITLES[code]?.[lang] ?? code.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
}
