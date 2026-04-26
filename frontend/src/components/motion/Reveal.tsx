"use client";

import { motion, useReducedMotion } from "framer-motion";
import { createElement, type CSSProperties, type ReactNode } from "react";

type RevealTag = "div" | "section" | "h1" | "h2" | "h3" | "p" | "span";

type RevealProps = {
  as?: RevealTag;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
  delay?: number;
  distance?: number;
  duration?: number;
  amount?: number;
  once?: boolean;
};

export default function Reveal({
  as = "div",
  className,
  style,
  children,
  delay = 0,
  distance = 22,
  duration = 0.55,
  amount = 0.2,
  once = true,
}: RevealProps) {
  const reduceMotion = useReducedMotion();
  const motionMap = {
    div: motion.div,
    section: motion.section,
    h1: motion.h1,
    h2: motion.h2,
    h3: motion.h3,
    p: motion.p,
    span: motion.span,
  } as const;

  if (reduceMotion) {
    return createElement(as, { className, style }, children);
  }

  const MotionTag = motionMap[as];

  return createElement(
    MotionTag,
    {
      className,
      style,
      initial: { opacity: 0, y: distance, filter: "blur(6px)" },
      whileInView: { opacity: 1, y: 0, filter: "blur(0px)" },
      viewport: { once, amount },
      transition: { duration, delay, ease: [0.22, 1, 0.36, 1] },
    },
    children
  );
}
