"use client";
import { useAppStore } from "@/store/app-store";
import { useNodeDetail } from "@/hooks/useNodeDetail";
import ProvenanceTimeline from "@/components/provenance/ProvenanceTimeline";

const SAMPLE_STEPS = [
  { kind: "ingest", source: "gmail/thread-4f2a", at: "2026-04-21 09:14", note: "subject + body extracted" },
  { kind: "extract", source: "gemini-2.5-pro", at: "2026-04-21 09:14", note: "entities → Person, Org" },
  { kind: "link", source: "graph/store", at: "2026-04-21 09:14", note: "SENT, RECEIVED edges written" },
  { kind: "review", source: "human:rishabh", at: "2026-04-22 11:02", note: "confirmed sender identity" },
];

function SampleProvenance() {
  return (
    <div className="flex flex-col h-full bg-bg overflow-y-auto">
      <div className="px-4 py-3 border-b border-border-color flex items-center justify-between">
        <p className="text-xs font-medium text-text-tertiary uppercase tracking-widest">Provenance</p>
        <span className="text-[10px] font-mono text-text-tertiary border border-border-color rounded px-1.5 py-0.5">
          PREVIEW
        </span>
      </div>
      <div className="px-4 py-3 space-y-1">
        <p className="text-xs text-text-secondary leading-relaxed">
          Click any node to see its real trace.
        </p>
        <p className="text-[11px] text-text-tertiary leading-relaxed">
          Every fact is grounded — source, model, reviewer, timestamp.
        </p>
      </div>
      <div className="px-4 pb-4 space-y-2">
        {SAMPLE_STEPS.map((s, i) => (
          <div
            key={i}
            className="rounded-md border border-border-color-subtle bg-bg-hover/40 px-3 py-2 space-y-1"
          >
            <div className="flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-wider text-text-tertiary font-mono">
                {s.kind}
              </span>
              <span className="text-[10px] text-text-tertiary font-mono">{s.at}</span>
            </div>
            <p className="text-xs text-text-secondary font-mono truncate">{s.source}</p>
            <p className="text-[11px] text-text-tertiary leading-snug">{s.note}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ProvenancePanel() {
  const { selectedNodeId } = useAppStore();
  const { data: node, isLoading } = useNodeDetail(selectedNodeId);

  if (!selectedNodeId) {
    return <SampleProvenance />;
  }

  if (isLoading) {
    return (
      <div className="p-4 space-y-2 bg-bg">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-10 rounded-lg bg-bg-hover animate-pulse" />
        ))}
      </div>
    );
  }

  if (!node) return null;

  return (
    <div className="flex flex-col h-full bg-bg overflow-y-auto">
      <div className="px-4 py-3 border-b border-border-color">
        <p className="text-xs font-medium text-text-tertiary uppercase tracking-widest">Provenance</p>
        <p className="text-xs font-mono text-text-mono mt-1 truncate">{node.id}</p>
      </div>
      <ProvenanceTimeline provenance={node.provenance} />
    </div>
  );
}
