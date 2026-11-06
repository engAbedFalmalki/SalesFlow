"use client";

import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { LoaderCircle } from "lucide-react";
import { useReducedMotion } from "motion/react";
import { useEffect, useState, type MouseEventHandler, type ReactNode, type RefObject } from "react";
import AnimatedContent from "../reactbits/AnimatedContent";
import BlurText from "../reactbits/BlurText";
import CountUp from "../reactbits/CountUp";
import LightRays from "../reactbits/LightRays";
import SplitText from "../reactbits/SplitText";
import SpotlightCard from "../reactbits/SpotlightCard";
import StarBorder from "../reactbits/StarBorder";
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
      className={TITLE_CLASS}
    />
  );
}

const TITLE_CLASS = "text-[1.75rem] font-semibold leading-tight tracking-[-0.01em]";

/** Home page title: words come into focus once on load (React Bits BlurText), without overshoot. */
export function HeroTitle({ text }: { text: string }) {
  return (
    <BlurText
      key={text}
      tag="h1"
      text={text}
      trigger="load"
      animateBy="words"
      delay={90}
      stepDuration={0.7}
      animationFrom={{ filter: "blur(8px)", opacity: 0, y: -10 }}
      animationTo={[{ filter: "blur(0px)", opacity: 1, y: 0 }]}
      easing={[0.22, 1, 0.36, 1]}
      className={TITLE_CLASS}
    />
  );
}

/** Resolved value of a colour token (canvas and WebGL can't read CSS variables). Empty until mounted. */
function useCssColor(token: string): string {
  const [value, setValue] = useState("");
  useEffect(() => setValue(getComputedStyle(document.documentElement).getPropertyValue(token).trim()), [token]);
  return value;
}

/**
 * Faint light rays in the accent colour behind the home page header (React Bits LightRays).
 * Decorative only: no pointer tracking or click bursts, fades out towards the content,
 * and holds still under prefers-reduced-motion.
 */
export function HeroRays() {
  const color = useCssColor("--color-accent");
  const reduced = useReducedMotion();
  if (!color) return null;
  return (
    <div aria-hidden className="hero-rays pointer-events-none absolute -inset-x-4 -top-6 -z-10 h-[calc(100%+4rem)] sm:-inset-x-6 lg:-inset-x-10 lg:-top-8">
      <LightRays
        raysOrigin="top-center"
        raysColor={color}
        lightMode
        raysSpeed={reduced ? 0 : 0.45}
        intro={!reduced}
        lightSpread={0.8}
        rayLength={1.8}
        intensity={1.9}
        opacity={0.8}
        glow={0.6}
        haze={0.2}
        followMouse={false}
        cursorShadow={false}
        clickPulse={false}
        maxDevicePixelRatio={1.5}
      />
    </div>
  );
}

type PrimaryActionProps = {
  children: ReactNode; href?: string; onClick?: MouseEventHandler; disabled?: boolean; busy?: boolean; className?: string;
};

/**
 * A page's main action, with a slow light travelling round its border (React Bits StarBorder).
 * Same size, colours and states as Button variant="primary"; renders a link when given href.
 */
export function PrimaryAction({ children, href, onClick, disabled, busy, className }: PrimaryActionProps) {
  const star = useCssColor("--color-accent-soft");
  const off = disabled || busy;
  const common = {
    theme: "dark" as const,
    color: star || undefined,
    backgroundColor: "var(--sf-primary-bg, var(--color-accent))",
    textColor: "white",
    radius: 6,
    duration: 6,
    trailLength: 0.35,
    glow: 0.35,
    hover: "brighten" as const,
    clickPulse: false,
    className: ["h-10 gap-1.5 px-4! py-0! text-sm hover:[--sf-primary-bg:var(--color-accent-strong)]",
      href && off ? "pointer-events-none opacity-50" : "", className].filter(Boolean).join(" "),
  };
  const content = <>{busy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}{children}</>;
  if (href !== undefined) {
    // A disabled link keeps its href (as before) but can't be clicked or tabbed to.
    return <StarBorder {...common} as="a" href={href || undefined} aria-disabled={off || undefined} tabIndex={off ? -1 : undefined} onClick={onClick}>{content}</StarBorder>;
  }
  return <StarBorder {...common} onClick={onClick} disabled={off} aria-busy={busy || undefined}>{content}</StarBorder>;
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
