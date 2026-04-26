"use client";

import { AnimatePresence, motion, useReducedMotion, useScroll, useTransform } from "framer-motion";
import { ArrowRight, WandSparkles } from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { FormEvent, Suspense, useEffect, useMemo, useRef, useState } from "react";

import Reveal from "@/components/motion/Reveal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiPost } from "@/lib/api-client";

const ScrollSphere = dynamic(() => import("@/components/hero/ScrollSphere"), { ssr: false });

type QueryHit = {
  id: string;
  preview: string;
};

type QueryResult = {
  answer: string | null;
  items: QueryHit[];
  tier_used: string;
  relevance: number;
  latency_ms: number;
};

const ROTATING_PROMPTS = [
  "Summarize key senders mentioned in this dataset.",
  "Find communication patterns between teams this week.",
  "Which threads discuss outages or escalations?",
  "Show the most active people and why they stand out.",
];

export default function HomePage() {
  const router = useRouter();
  const rootRef = useRef<HTMLElement>(null);
  const reduceMotion = useReducedMotion();
  const [query, setQuery] = useState("");
  const [activePrompt, setActivePrompt] = useState(0);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { scrollYProgress } = useScroll({
    target: rootRef,
    offset: ["start start", "end end"],
  });
  const sphereY = useTransform(scrollYProgress, [0, 0.45], [0, -48]);
  const sphereOpacity = useTransform(scrollYProgress, [0, 0.45], [1, 0.85]);
  const headingY = useTransform(scrollYProgress, [0, 0.32], [0, 28]);
  const headingOpacity = useTransform(scrollYProgress, [0, 0.4], [1, 0.9]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setActivePrompt((current) => (current + 1) % ROTATING_PROMPTS.length);
    }, 2600);
    return () => window.clearInterval(timer);
  }, []);

  const topHits = useMemo(() => (result?.items ?? []).slice(0, 3), [result]);

  async function runQuery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;

    setIsPending(true);
    setError(null);
    try {
      const data = await apiPost<QueryResult>("/api/query", {
        query: trimmed,
        context: { max_latency_ms: 30000 },
      });
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Query failed.");
      setResult(null);
    } finally {
      setIsPending(false);
    }
  }

  return (
    <main
      ref={rootRef}
      data-lenis-root
      className="relative h-screen overflow-y-auto bg-[#050505] text-[#f2f2f2]"
    >
      <section className="relative h-screen overflow-hidden border-b border-white/10">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(255,255,255,0.07),transparent_38%),radial-gradient(circle_at_80%_15%,rgba(255,255,255,0.06),transparent_36%),linear-gradient(160deg,#040404_0%,#0b0b0b_55%,#080808_100%)]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_34%,rgba(96,165,250,0.08)_0%,transparent_60%)]" />

        <motion.div
          className="pointer-events-none absolute inset-x-0 top-[15vh] z-0 h-[52vh] will-change-transform"
          style={
            reduceMotion
              ? undefined
              : { y: sphereY, opacity: sphereOpacity }
          }
        >
          <Suspense fallback={<div className="h-full w-full" />}>
            <ScrollSphere />
          </Suspense>
        </motion.div>

        <motion.div
          className="absolute inset-x-0 bottom-0 z-10 mx-auto flex max-w-6xl flex-col items-center px-6 pb-28 text-center md:pb-32"
          style={reduceMotion ? undefined : { y: headingY, opacity: headingOpacity }}
        >
          <Reveal
            as="h1"
            delay={0.02}
            amount={0.25}
            className="text-balance text-4xl font-semibold leading-tight md:text-6xl"
          >
            Your company&apos;s memory.
            <span className="mt-2 block bg-gradient-to-r from-white to-zinc-400 bg-clip-text text-transparent md:whitespace-nowrap">
              Made legible with Better Context.
            </span>
          </Reveal>

          <Reveal
            as="p"
            delay={0.12}
            amount={0.2}
            className="mt-5 max-w-2xl text-pretty text-sm leading-relaxed text-zinc-400 md:text-lg"
          >
            Turn fragmented enterprise data into a queryable knowledge graph with strict provenance,
            grounded retrieval, and workflow-ready context.
          </Reveal>
        </motion.div>
      </section>

      <section className="relative min-h-screen overflow-hidden px-5 py-16 md:px-10">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_15%_20%,rgba(255,255,255,0.05),transparent_35%),radial-gradient(circle_at_86%_80%,rgba(255,255,255,0.04),transparent_30%)]" />

        <Reveal
          as="div"
          delay={0.02}
          amount={0.25}
          distance={40}
          className="relative mx-auto flex min-h-[calc(100vh-8rem)] w-full max-w-4xl items-center justify-center"
        >
          <div className="w-full rounded-3xl border border-white/15 bg-black/55 p-5 shadow-[0_25px_70px_rgba(0,0,0,0.55)] backdrop-blur-sm md:p-8">
            <Reveal as="h2" delay={0.06} className="text-center text-2xl font-semibold md:text-4xl">
              Ask your graph with grounded context.
            </Reveal>
            <Reveal as="p" delay={0.12} className="mt-3 text-center text-sm leading-relaxed text-zinc-400 md:text-lg">
              Queries run against your existing retrieval API and return tier, relevance, and top evidence.
            </Reveal>

            <div className="mt-6 flex min-h-10 justify-center">
              <AnimatePresence mode="wait">
                <motion.button
                  key={ROTATING_PROMPTS[activePrompt]}
                  type="button"
                  onClick={() => setQuery(ROTATING_PROMPTS[activePrompt])}
                  initial={{ opacity: 0, y: 8, filter: "blur(5px)" }}
                  animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                  exit={{ opacity: 0, y: -8, filter: "blur(5px)" }}
                  transition={{ duration: 0.22 }}
                  className="inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/5 px-4 py-2 text-left text-sm text-zinc-200 transition hover:border-white/35 hover:bg-white/10"
                >
                  <WandSparkles className="size-4 text-zinc-300" />
                  {ROTATING_PROMPTS[activePrompt]}
                </motion.button>
              </AnimatePresence>
            </div>

            <form onSubmit={runQuery} className="mt-4">
              <div className="mx-auto flex max-w-3xl flex-col gap-3 rounded-2xl border border-white/15 bg-[#0c0c0c] p-3 md:flex-row">
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Ask a context question..."
                  className="h-11 rounded-xl border-white/20 bg-black/70 px-4 text-sm text-white placeholder:text-zinc-500"
                />
                <Button
                  type="submit"
                  disabled={isPending || !query.trim()}
                  className="h-11 rounded-xl border border-white/20 bg-white text-black hover:bg-zinc-200"
                >
                  {isPending ? "Querying..." : "Run query"}
                </Button>
              </div>
            </form>

            {error ? (
              <div className="mt-4 rounded-xl border border-white/20 bg-white/5 px-4 py-3 text-sm text-zinc-300">
                {error}
              </div>
            ) : null}

            {result ? (
              <div className="mt-4 space-y-3 rounded-xl border border-white/15 bg-[#101010] p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-400">
                  <span className="rounded-full border border-white/20 px-2.5 py-1">tier: {result.tier_used}</span>
                  <span className="rounded-full border border-white/20 px-2.5 py-1">
                    relevance: {result.relevance.toFixed(2)}
                  </span>
                  <span className="rounded-full border border-white/20 px-2.5 py-1">
                    latency: {result.latency_ms}ms
                  </span>
                </div>
                <div className="space-y-2 text-sm text-zinc-300">
                  {topHits.length ? (
                    topHits.map((hit) => (
                      <div key={hit.id} className="rounded-lg border border-white/10 bg-black/45 p-3">
                        <p className="text-xs text-zinc-500">{hit.id}</p>
                        <p className="mt-1 line-clamp-2">{hit.preview}</p>
                      </div>
                    ))
                  ) : (
                    <p>No hits returned.</p>
                  )}
                </div>
              </div>
            ) : null}

            <div className="mt-7 flex flex-col items-center">
              <p className="text-xs uppercase tracking-[0.28em] text-zinc-500">OR</p>
              <button
                type="button"
                onClick={() => router.push("/app")}
                className="mt-3 inline-flex items-center gap-2 rounded-full border border-white/25 bg-white/5 px-5 py-2.5 text-sm font-medium text-white transition hover:border-white/45 hover:bg-white/10"
              >
                Explore the graph
                <ArrowRight className="size-4" />
              </button>
            </div>
          </div>
        </Reveal>
      </section>
    </main>
  );
}
