"use client";

import Link from "next/link";
import { useI18n } from "@/lib/i18n";

export default function NotFound() {
  const { t } = useI18n();
  return (
    <div className="py-20">
      <h1 className="text-2xl font-semibold">{t("nf.title")}</h1>
      <p className="mt-2 text-muted">{t("nf.body")}</p>
      <Link href="/" className="mt-4 inline-block font-medium text-accent underline">{t("nf.link")}</Link>
    </div>
  );
}
