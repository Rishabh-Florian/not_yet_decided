"use client";
import { useState } from "react";
import { usePatternQuery } from "@/hooks/usePatternQuery";
import { nodeTypeColor, nodeDisplayName, NODE_TYPES } from "@/lib/utils";
import ConfidencePill from "@/components/provenance/ConfidencePill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useRouter } from "next/navigation";
import { useAppStore } from "@/store/app-store";
import type { PatternMatch } from "@/types/api";
import { Search, Info } from "lucide-react";

// Only show presets that have data in this deployment
const PRESETS = [
  { pattern: "(Person)-[SENT]->(Message)",    desc: "Who sent what messages" },
  { pattern: "(Message)-[RECEIVED]->(Person)", desc: "Who received each message" },
];

function tokenize(pattern: string) {
  return pattern.split(/(\([^)]*\)|\[[^\]]*\]|->)/g).map((part, i) => {
    if (part.startsWith("(")) {
      const type = part.slice(1, -1);
      const color = NODE_TYPES.includes(type as typeof NODE_TYPES[number])
        ? nodeTypeColor(type)
        : "#8a8a8a";
      return <span key={i} style={{ color }} className="font-medium">{part}</span>;
    }
    if (part.startsWith("[")) return <span key={i} className="text-accent font-medium">{part}</span>;
    return <span key={i} className="text-text-tertiary">{part}</span>;
  });
}

export default function QueryView() {
  const router = useRouter();
  const { setSelectedNodeId } = useAppStore();
  const [pattern, setPattern] = useState("");
  const { mutate, data: results, isPending, isError, error } = usePatternQuery();

  const run = (p = pattern) => {
    const trimmed = p.trim();
    if (trimmed) mutate({ pattern: trimmed, limit: 100 });
  };

  return (
    <div className="h-full flex flex-col bg-bg">
      {/* Header */}
      <div className="px-5 py-4 border-b border-border-color bg-bg-card space-y-3">
        <p className="text-xs font-medium text-text-tertiary uppercase tracking-widest">Pattern Query</p>

        {/* Syntax hint */}
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-accent/5 border border-accent/15 text-xs text-text-secondary">
          <Info size={12} className="text-accent mt-0.5 shrink-0" />
          <span>
            Syntax: <code className="font-mono text-accent">(NodeType)-[RELATION]-&gt;(NodeType)</code>
            {" "}— e.g. <code className="font-mono text-accent">(Person)-[SENT]-&gt;(Message)</code>
          </span>
        </div>

        {/* Search input */}
        <div className="flex gap-2">
          <Input
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            placeholder="(Person)-[SENT]->(Message)"
            className="font-mono text-sm bg-bg border-border-color text-text-primary placeholder:text-text-tertiary"
            autoFocus
          />
          <Button
            onClick={() => run()}
            disabled={isPending || !pattern.trim()}
            className="bg-accent hover:bg-accent-dim text-white gap-1.5 shrink-0"
          >
            <Search size={14} />
            {isPending ? "Running…" : "Query"}
          </Button>
        </div>

        {/* Presets */}
        <div className="space-y-1">
          <p className="text-xs text-text-tertiary">Quick patterns:</p>
          <div className="flex flex-col gap-1.5">
            {PRESETS.map(({ pattern: p, desc }) => (
              <button
                key={p}
                onClick={() => { setPattern(p); run(p); }}
                className="flex items-center justify-between px-3 py-2 rounded-lg text-xs border border-border-color bg-bg hover:border-accent/40 hover:bg-accent-bg transition-colors text-left"
              >
                <span className="font-mono">{tokenize(p)}</span>
                <span className="text-text-tertiary ml-3 shrink-0">{desc}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Results */}
      <div className="flex-1 overflow-auto">
        {isPending && (
          <div className="p-4 space-y-2">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="h-11 bg-bg-hover rounded-lg animate-pulse" />
            ))}
          </div>
        )}

        {isError && (
          <div className="flex flex-col items-center justify-center h-48 gap-2">
            <p className="text-sm text-text-secondary">
              Query failed —{" "}
              {(error as Error)?.message?.includes("400")
                ? "check your pattern syntax"
                : "is the backend running?"}
            </p>
            <p className="text-xs text-text-tertiary font-mono">
              {(error as Error)?.message}
            </p>
          </div>
        )}

        {results && (
          <>
            <div className="px-5 py-2.5 border-b border-border-color flex items-center gap-3 bg-bg-card">
              <span className="text-xs font-medium text-text-secondary">
                {results.total.toLocaleString()} result{results.total !== 1 ? "s" : ""}
              </span>
              <span className="font-mono text-xs text-accent">{results.pattern}</span>
            </div>

            {results.total === 0 ? (
              <div className="flex flex-col items-center justify-center h-48 gap-2">
                <p className="text-sm text-text-secondary">No matches found.</p>
                <p className="text-xs text-text-tertiary">
                  This relation type may not exist in the current dataset.
                </p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-bg-card border-b border-border-color z-10">
                  <tr>
                    {["Source", "Relation", "Target"].map((h) => (
                      <th key={h} className="px-4 py-2 text-left text-xs text-text-tertiary font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {results.matches.map((m: PatternMatch, i: number) => (
                    <tr
                      key={i}
                      className="border-b border-border-color-subtle hover:bg-bg-hover transition-colors"
                    >
                      <td className="px-4 py-2.5">
                        <button
                          onClick={() => {
                            setSelectedNodeId(m.source.id);
                            router.push(`/app/nodes/${encodeURIComponent(m.source.id)}`);
                          }}
                          className="flex items-center gap-1.5 group text-left"
                        >
                          <div className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: nodeTypeColor(m.source.type) }} />
                          <span className="text-text-primary group-hover:text-accent transition-colors truncate max-w-[160px] text-sm">
                            {nodeDisplayName(m.source)}
                          </span>
                        </button>
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          <span className="text-xs px-2 py-0.5 rounded-full font-mono font-medium bg-accent-bg text-accent">
                            {m.edge.relation_type}
                          </span>
                          <ConfidencePill confidence={m.edge.confidence} />
                        </div>
                      </td>
                      <td className="px-4 py-2.5">
                        <button
                          onClick={() => {
                            setSelectedNodeId(m.target.id);
                            router.push(`/app/nodes/${encodeURIComponent(m.target.id)}`);
                          }}
                          className="flex items-center gap-1.5 group text-left"
                        >
                          <div className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: nodeTypeColor(m.target.type) }} />
                          <span className="text-text-primary group-hover:text-accent transition-colors truncate max-w-[160px] text-sm">
                            {nodeDisplayName(m.target)}
                          </span>
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}

        {!results && !isPending && !isError && (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <div className="w-10 h-10 rounded-full bg-bg-hover flex items-center justify-center">
              <Search size={16} className="text-text-tertiary" />
            </div>
            <p className="text-sm text-text-secondary">Pick a pattern above or type your own.</p>
          </div>
        )}
      </div>
    </div>
  );
}
