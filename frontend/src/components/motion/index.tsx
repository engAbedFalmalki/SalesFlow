"use client";

import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import type { ReactNode, RefObject } from "react";
import AnimatedContent from "../reactbits/AnimatedContent";
import CountUp from "../reactbits/CountUp";
import SplitText from "../reactbits/SplitText";
import SpotlightCard from "../reactbits/SpotlightCard";
import { useI18n } from "@/lib/i18n";

gsap.registerPlugin(ScrollTrigger, useGSAP);

/** Scroll-triggered entrance for a block of content (React Bits AnimatedContent, app defaults). */
export function Reveal({ children, className, delay = 0 }: { children: ReactNode; className?: string; delay?: number }) {
  return (
    <AnimatedContent distance={28} duration={0.7} ease="power3.out" threshold={0.08} delay={delay} className={className}>
      {children}
    </AnimatedContent>
  );
}

/** Page title split into words that rise in once on load (React Bits SplitText). */
export function PageTitle({ text }: { text: string }) {
  return (
    <SplitText
      text={text}
      tag="h1"
      splitType="words"
      trigger="load"
      textAlign="start"
      delay={60}
      duration={0.8}
      from={{ opacity: 0, y: 24 }}
      to={{ opacity: 1, y: 0 }}
      className="text-[1.75rem] font-semibold leading-tight tracking-[-0.01em]"
    />
  );
}

/**
 * Counting KPI figure (React Bits CountUp). Display only: it lands on the same text
 * the static formatter produces, and the exact decimal value stays in the parent's
 * title attribute and in the API.
 */
export function Figure({ value, kind }: { value: string | number | null; kind: "money" | "count" }) {
  const { lang } = useI18n();
  if (value === null || value === undefined || value === "") return <>{lang === "ar" ? "غير متاح" : "n/a"}</>;
  const n = Number(value);
  if (kind === "count") return <CountUp key={lang} to={n} separator="," duration={1.2} trigger="load" />;
  // Same text as format.moneyCompact: "SAR 1.47M" in English, "1.47M ر.س" in Arabic (Latin digits in both).
  const cur = lang === "ar" ? { suffix: " ر.س" } : { prefix: "SAR " };
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return <CountUp key={lang} to={n} {...cur} format="compact" decimals={2} duration={1.2} trigger="load" />;
  if (abs >= 10_000) return <CountUp key={lang} to={n} {...cur} format="compact" decimals={1} duration={1.2} trigger="load" />;
  return <CountUp key={lang} to={n} {...cur} separator="," decimals={2} duration={1.2} trigger="load" />;
}

/** Panel with a soft pointer-following spotlight in the accent colour (React Bits SpotlightCard). */
export function SpotlightPanel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <SpotlightCard
      theme="light"
      spotlightColor="var(--color-accent)"
      intensity={0.35}
      borderGlow={0.5}
      spotlightSize={260}
      proximity={60}
      flare={false}
      className={["rounded-lg", className].filter(Boolean).join(" ")}
      style={{
        // Use the app's tokens instead of the component's built-in palette.
        ["--spotlight-card-surface" as string]: "var(--color-surface)",
        ["--spotlight-card-border" as string]: "var(--color-line)",
        ["--spotlight-card-shadow" as string]: "none",
      }}
    >
      {children}
    </SpotlightCard>
  );
}

/**
 * Bars marked with data-bar grow from zero to their value when scrolled into view,
 * so the motion encodes the data (each bar travels to its own length).
 */
export function useBarReveal(scope: RefObject<HTMLElement | null>, deps: unknown[]) {
  useGSAP(() => {
    const bars = scope.current?.querySelectorAll<HTMLElement>("[data-bar]");
    if (!bars?.length) return;
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      const rtl = getComputedStyle(scope.current!).direction === "rtl";
      gsap.from(bars, {
        scaleX: 0,
        transformOrigin: rtl ? "right center" : "left center", // bars grow from their start edge
        duration: 0.9,
        ease: "power3.out",
        stagger: 0.05,
        scrollTrigger: { trigger: scope.current, start: "top 85%", once: true },
      });
    });
    return () => mm.revert();
  }, { scope, dependencies: deps, revertOnUpdate: true });
}
