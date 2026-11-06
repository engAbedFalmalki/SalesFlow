"use client";

import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Lenis from "lenis";
import "lenis/dist/lenis.css";
import { usePathname } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";

gsap.registerPlugin(ScrollTrigger);

/**
 * Lenis smooth scrolling for the page, driven by the GSAP ticker so ScrollTrigger
 * and Lenis share one frame loop.
 *
 * Data-app safeguards:
 * - allowNestedScroll: tables and lists with their own overflow keep native scrolling.
 * - Dialogs/drawers and tooltips are excluded, and Lenis pauses while Radix locks body scroll.
 * - prefers-reduced-motion: Lenis's default turns smoothing off (1:1 scroll).
 * - ScrollTrigger positions are refreshed when page height changes (data arrives after load).
 */
export function SmoothScroll({ children }: { children: ReactNode }) {
  const lenisRef = useRef<Lenis | null>(null);
  const path = usePathname();

  useEffect(() => {
    const lenis = new Lenis({
      lerp: 0.12,
      allowNestedScroll: true,
      anchors: true,
      stopInertiaOnNavigate: true,
      prevent: (node) => !!node.closest?.('[role="dialog"], [data-radix-popper-content-wrapper], [data-lenis-prevent]'),
    });
    lenisRef.current = lenis;
    // Lenis turns smoothing off under prefers-reduced-motion; expose the effective state.
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const mark = () => { document.documentElement.dataset.smoothScroll = reduced.matches ? "off" : "on"; };
    mark();
    reduced.addEventListener("change", mark);
    lenis.on("scroll", ScrollTrigger.update);
    const tick = (time: number) => lenis.raf(time * 1000);
    gsap.ticker.add(tick);
    gsap.ticker.lagSmoothing(0);

    const syncLock = () => (document.body.hasAttribute("data-scroll-locked") ? lenis.stop() : lenis.start());
    const lockObserver = new MutationObserver(syncLock);
    lockObserver.observe(document.body, { attributes: true, attributeFilter: ["data-scroll-locked"] });

    let timer: number | undefined;
    const sizeObserver = new ResizeObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => ScrollTrigger.refresh(), 150);
    });
    sizeObserver.observe(document.body);

    return () => {
      reduced.removeEventListener("change", mark);
      delete document.documentElement.dataset.smoothScroll;
      window.clearTimeout(timer);
      sizeObserver.disconnect();
      lockObserver.disconnect();
      gsap.ticker.remove(tick);
      lenis.destroy();
      lenisRef.current = null;
    };
  }, []);

  // New page: start at the top without a smooth scroll back up.
  useEffect(() => {
    lenisRef.current?.scrollTo(0, { immediate: true, force: true });
  }, [path]);

  return <>{children}</>;
}
