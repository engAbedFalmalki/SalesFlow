import type { Metadata } from "next";
import { IBM_Plex_Sans, IBM_Plex_Sans_Arabic } from "next/font/google";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { Shell } from "@/components/shell";
import { LANG_COOKIE, type Lang } from "@/lib/lang";
import "./globals.css";
import { Providers } from "./providers";

const plex = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex", display: "swap" });
// Arabic glyphs come from Plex Sans Arabic; digits and Latin text keep Plex Sans.
const plexAr = IBM_Plex_Sans_Arabic({ subsets: ["arabic"], weight: ["400", "500", "600"], variable: "--font-plex-ar", display: "swap" });

export const metadata: Metadata = {
  title: { default: "SalesFlow", template: "%s · SalesFlow" },
  description: "Sales data warehouse and analytics over synthetic e-commerce data.",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const lang: Lang = (await cookies()).get(LANG_COOKIE)?.value === "ar" ? "ar" : "en";
  return (
    <html lang={lang} dir={lang === "ar" ? "rtl" : "ltr"} className={`${plex.variable} ${plexAr.variable}`}>
      <body>
        <Providers lang={lang}>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
