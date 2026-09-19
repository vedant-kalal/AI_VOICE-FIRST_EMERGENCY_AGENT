import gsap from "gsap";
import { Flip } from "gsap/Flip";
import { useGSAP } from "@gsap/react";

gsap.registerPlugin(useGSAP, Flip);
if (import.meta.env.DEV) (window as unknown as { __gsap: typeof gsap }).__gsap = gsap; // console debugging only

export const prefersReducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Motion tokens (motion-system): durations in seconds for GSAP, eases matched to the CSS curves. */
export const DUR = { instant: 0.1, fast: 0.15, base: 0.25, slow: 0.35, deliberate: 0.5 } as const;
export const EASE = { out: "expo.out", in: "power3.in", inOut: "power2.inOut" } as const;
/** Sibling stagger, capped so item 7+ never arrives late. */
export const stagger = (i: number) => Math.min(i, 5) * 0.04;

export { gsap, Flip, useGSAP };
