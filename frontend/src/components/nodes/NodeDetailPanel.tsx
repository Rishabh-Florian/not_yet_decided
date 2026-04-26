"use client";
import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useNodeDetail } from "@/hooks/useNodeDetail";
import { useNeighbors } from "@/hooks/useNeighbors";
import { useAppStore } from "@/store/app-store";
import { nodeTypeColor, nodeDisplayName } from "@/lib/utils";
import ConfidencePill from "@/components/provenance/ConfidencePill";
import ProvenanceTimeline from "@/components/provenance/ProvenanceTimeline";
import { Button } from "@/components/ui/button";
import { Pencil, ArrowLeft, ChevronDown, ArrowRight, Cpu, User, Wand2 } from "lucide-react";

const METHOD_ICON: Record<string, React.ElementType> = {
  direct_mapping: ArrowRight,
  llm_extraction: Cpu,
  rule_based:     Wand2,
  human:          User,
  synthetic:      Wand2,
};
const METHOD_COLOR: Record<string, string> = {
  direct_mapping: "#e0e0e0",
  llm_extraction: "#c2c2c2",
  rule_based:     "#aaaaaa",
  human:          "#969696",
  synthetic:      "#7c9eff",
};

export default function NodeDetailPanel({ id }: { id: string }) {
  const router = useRouter();
  const { setSelectedNodeId, setProvenanceFocusField } = useAppStore();
  const { data: node, isLoading } = useNodeDetail(id);
  const { data: neighbors } = useNeighbors(id, 1);
  const [focusField, setFocusField] = useState<string | null>(null);

  useEffect(() => { setSelectedNodeId(id); return () => setSelectedNodeId(null); }, [id, setSelectedNodeId]);

  if (isLoading) {
    return (
      <div className="p-6 space-y-3 bg-bg">
        <div className="h-7 w-48 bg-bg-hover rounded-lg animate-pulse" />
        <div className="h-4 w-32 bg-bg-hover rounded animate-pulse" />
        {[...Array(5)].map((_, i) => <div key={i} className="h-9 bg-bg-hover rounded-lg animate-pulse" />)}
      </div>
    );
  }

  if (!node) return <div className="p-6 text-text-secondary text-sm bg-bg">Node not found.</div>;

  const color = nodeTypeColor(node.type);

  return (
    <div className="h-full overflow-auto bg-bg">
      <div className="px-5 py-4 border-b border-border-color bg-bg-card">
        <button onClick={() => router.back()}
          className="flex items-center gap-1 text-xs text-text-tertiary hover:text-accent mb-3 transition-colors">
          <ArrowLeft size={12} /> Back
        </button>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 mb-1.5 flex-wrap">
              <span className="text-xs px-2 py-0.5 rounded-full font-medium"
                style={{ background: color + "18", color }}>
                {node.type}
              </span>
              <ConfidencePill confidence={node.confidence} />
              <span className="text-xs text-text-tertiary font-mono">v{node.version}</span>
            </div>
            <h1 className="text-lg font-semibold text-text-primary truncate">{nodeDisplayName(node)}</h1>
            <p className="text-xs font-mono text-text-tertiary mt-0.5 truncate">{node.id}</p>
          </div>
          <Button size="sm" variant="outline"
            className="shrink-0 border-border-color text-text-secondary hover:text-accent hover:border-accent/40 gap-1.5 text-xs"
            onClick={() => router.push(`/app/edit/${encodeURIComponent(id)}`)}>
            <Pencil size={11} /> Edit
          </Button>
        </div>
      </div>

      <div className="px-5 py-4">
        <p className="text-xs font-medium text-text-tertiary uppercase tracking-widest mb-2">Attributes</p>
        <div className="space-y-0.5 rounded-xl border border-border-color overflow-hidden bg-bg-card">
          {Object.entries(node.attributes).map(([key, val], idx, arr) => {
            const provForField = node.provenance.filter((p) =>
              p.source_field?.toLowerCase().includes(key.toLowerCase())
            );
            const prov0 = provForField[0];
            const method = prov0?.extraction_method;
            const mColor = method ? (METHOD_COLOR[method] ?? "#727272") : undefined;
            const MIcon = method ? (METHOD_ICON[method] ?? Wand2) : undefined;
            const sourceFile = prov0?.source_file;
            const shortSource = sourceFile
              ? sourceFile.split("/").slice(-2).join("/")
              : null;
            return (
              <button key={key} onClick={() => { setProvenanceFocusField(key); setFocusField(f => f === key ? null : key); }}
                className={`w-full flex items-start gap-3 px-3 py-2.5 hover:bg-bg-hover transition-colors text-left ${idx < arr.length - 1 ? "border-b border-border-color-subtle" : ""}`}>
                <span className="text-xs font-mono text-text-tertiary w-28 shrink-0 pt-0.5 truncate">{key}</span>
                <div className="flex-1 min-w-0">
                  <span className="text-sm text-text-primary truncate block">{String(val)}</span>
                  {shortSource && mColor && MIcon && (
                    <span className="flex items-center gap-1 mt-0.5">
                      <MIcon size={9} style={{ color: mColor }} />
                      <span className="text-[10px] font-mono truncate" style={{ color: mColor + "cc" }}>
                        {shortSource}
                      </span>
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {node.provenance.length > 0 && (
        <div className="px-5 py-4 border-t border-border-color">
          <button
            onClick={() => setFocusField(f => f === "__all__" ? null : "__all__")}
            className="flex items-center justify-between w-full mb-2 group"
          >
            <p className="text-xs font-medium text-text-tertiary uppercase tracking-widest">
              Provenance
              {focusField && focusField !== "__all__" && (
                <span className="ml-1.5 font-mono normal-case text-accent">· {focusField}</span>
              )}
            </p>
            <ChevronDown
              size={12}
              className={`text-text-tertiary transition-transform ${focusField ? "rotate-180" : ""}`}
            />
          </button>
          {focusField && (
            <div className="rounded-xl border border-border-color overflow-hidden bg-bg-card">
              <ProvenanceTimeline
                provenance={
                  focusField === "__all__"
                    ? node.provenance
                    : node.provenance.filter(p => p.source_field?.toLowerCase().includes(focusField.toLowerCase()))
                }
              />
            </div>
          )}
          {!focusField && (
            <p className="text-xs text-text-tertiary">
              Click an attribute above to trace it to its source.
            </p>
          )}
        </div>
      )}

      {neighbors && neighbors.neighbors.length > 0 && (
        <div className="px-5 py-4 border-t border-border-color">
          <p className="text-xs font-medium text-text-tertiary uppercase tracking-widest mb-2">
            Neighbors ({neighbors.neighbors.length})
          </p>
          <div className="space-y-0.5 rounded-xl border border-border-color overflow-hidden bg-bg-card">
            {neighbors.neighbors.slice(0, 12).map((n, idx, arr) => {
              const nc = nodeTypeColor(n.type);
              return (
                <button key={n.id}
                  onClick={() => { setSelectedNodeId(n.id); router.push(`/app/nodes/${encodeURIComponent(n.id)}`); }}
                  className={`w-full flex items-center gap-2.5 px-3 py-2 hover:bg-bg-hover transition-colors text-left ${idx < arr.length - 1 ? "border-b border-border-color-subtle" : ""}`}>
                  <div className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: nc }} />
                  <span className="text-xs text-text-tertiary font-medium w-20 shrink-0">{n.type}</span>
                  <span className="text-sm text-text-primary truncate">{nodeDisplayName(n)}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
