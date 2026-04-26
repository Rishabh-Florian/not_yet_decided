"use client";
import { useAppStore } from "@/store/app-store";
import { useNodeDetail } from "@/hooks/useNodeDetail";
import ProvenanceTimeline from "@/components/provenance/ProvenanceTimeline";
import { Info } from "lucide-react";

export default function ProvenancePanel() {
  const { selectedNodeId } = useAppStore();
  const { data: node, isLoading } = useNodeDetail(selectedNodeId);

  if (!selectedNodeId) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 px-6 text-center bg-bg">
        <div className="w-8 h-8 rounded-full bg-bg-hover flex items-center justify-center">
          <Info size={14} className="text-text-tertiary" />
        </div>
        <p className="text-xs text-text-tertiary leading-relaxed">
          Select a node to inspect its provenance trace.
        </p>
      </div>
    );
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
