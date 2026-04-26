"use client";

import { useReducedMotion } from "framer-motion";
import { useEffect } from "react";

export default function LenisProvider() {
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    if (reduceMotion) return;

    let cancelled = false;
    let rafId = 0;
    let lenis: { raf: (time: number) => void; destroy: () => void } | null = null;

    const start = async () => {
      const { default: Lenis } = await import("lenis");
      if (cancelled) return;

      const wrapper = document.querySelector<HTMLElement>("[data-lenis-root]");
      lenis = new Lenis(
        wrapper
          ? {
              wrapper,
              content: wrapper,
              duration: 1.05,
              smoothWheel: true,
              wheelMultiplier: 0.95,
              touchMultiplier: 1,
            }
          : {
              duration: 1.05,
              smoothWheel: true,
              wheelMultiplier: 0.95,
              touchMultiplier: 1,
            }
      );

      const raf = (time: number) => {
        lenis?.raf(time);
        rafId = window.requestAnimationFrame(raf);
      };
      rafId = window.requestAnimationFrame(raf);
    };

    void start();

    return () => {
      cancelled = true;
      window.cancelAnimationFrame(rafId);
      lenis?.destroy();
    };
  }, [reduceMotion]);

  return null;
}
