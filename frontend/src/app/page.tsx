"use client";
import { motion } from "framer-motion";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import { ArrowRight } from "lucide-react";

const HeroScene = dynamic(() => import("@/components/hero/HeroScene"), { ssr: false });

const EASE = [0.16, 1, 0.3, 1] as const;

function fadeUp(delay: number) {
  return {
    initial: { opacity: 0, y: 18 },
    animate: { opacity: 1, y: 0, transition: { duration: 0.55, ease: EASE, delay } },
  };
}

export default function HeroPage() {
  const router = useRouter();

  return (
    <div
      className="relative w-screen h-screen overflow-hidden"
      style={{ background: "linear-gradient(160deg, #1a2744 0%, #131f3a 45%, #0e1829 100%)" }}
    >
      {/* Subtle noise/grain texture overlay for depth */}
      <div
        className="absolute inset-0 pointer-events-none opacity-[0.03]"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
          backgroundSize: "180px",
        }}
      />

      {/* Blue glow behind sphere center */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 60% 50% at 50% 36%, rgba(59,130,246,0.14) 0%, transparent 65%)",
        }}
      />

      {/* 3D canvas — occupies top 65%, offset down slightly */}
      <div className="absolute inset-x-0 top-0 h-[65%]" style={{ paddingTop: "2vh" }}>
        <HeroScene />
      </div>

      {/* Text content */}
      <div className="absolute inset-x-0 bottom-0 h-[38%] flex flex-col items-center justify-center px-6">
        <motion.div {...fadeUp(1.6)} className="text-center">
          <h1
            className="text-[2.8rem] md:text-[3.4rem] font-bold leading-tight tracking-tight"
            style={{ color: "#e8f0fe" }}
          >
            Your company&apos;s memory.
            <br />
            <span
              style={{
                background: "linear-gradient(90deg, #60a5fa, #818cf8)",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
              }}
            >
              Made legible with Better Context!
            </span>
          </h1>
        </motion.div>

        <motion.p
          {...fadeUp(2.0)}
          className="mt-4 text-[0.95rem] text-center leading-relaxed max-w-[460px]"
          style={{ color: "#8ba9d0" }}
        >
          Turn fragmented data into a knowledge graph AI can operate on —
          with fact-level provenance tracing every claim to its source.
        </motion.p>

        <motion.div {...fadeUp(2.4)} className="mt-7 flex items-center gap-4">
          <button
            onClick={() => router.push("/app")}
            className="flex items-center gap-2.5 px-7 py-3.5 rounded-lg text-sm font-semibold text-white transition-all hover:scale-[1.03] active:scale-[0.98]"
            style={{
              background: "linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)",
              boxShadow: "0 0 30px rgba(59,130,246,0.38), 0 4px 14px rgba(0,0,0,0.4)",
            }}
          >
            Explore the graph
            <ArrowRight size={15} />
          </button>
        </motion.div>
      </div>
    </div>
  );
}
