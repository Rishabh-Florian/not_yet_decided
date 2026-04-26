"use client";
import { useState } from "react";
import type { ProvenanceResponse } from "@/types/api";
import ConfidencePill from "./ConfidencePill";
import SourceRecordDrawer from "./SourceRecordDrawer";
import { ArrowRight, Cpu, User, Wand2, type LucideIcon } from "lucide-react";

const METHOD_META: Record<string, { label: string; color: string; Icon: LucideIcon }> = {
  direct_mapping: { label: "Direct",  color: "#2563eb", Icon: ArrowRight },
  llm_extraction: { label: "LLM",     color: "#d97706", Icon: Cpu       },
  rule_based:     { label: "Rule",    color: "#059669", Icon: Wand2     },
  human:          { label: "Human",   color: "#7c3aed", Icon: User      },
};

export default function ProvenanceTimeline({ provenance }: { provenance: ProvenanceResponse[] }) {
  const [drawerProv, setDrawerProv] = useState<ProvenanceResponse | null>(null);

  if (provenance.length === 0) {
    return <p className="px-4 py-6 text-xs text-text-tertiary text-center">No provenance records.</p>;
  }

  return (
    <div className="px-4 py-3 space-y-3">
      {provenance.map((p, i) => {
        const meta = METHOD_META[p.extraction_method] ?? METHOD_META.rule_based;
        const Icon = meta.Icon;
        return (
          <div key={p.id ?? i} className="relative">
            {i < provenance.length - 1 && (
              <div className="absolute left-[7px] top-5 bottom-[-12px] w-px bg-border-color" />
            )}
            <div className="flex gap-3">
              <div
                className="w-3.5 h-3.5 rounded-full mt-0.5 shrink-0 border"
                style={{ background: meta.color + "20", borderColor: meta.color + "60" }}
              />
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span
                    className="flex items-center gap-1 text-xs px-1.5 py-0.5 rounded-md font-medium"
                    style={{ background: meta.color + "12", color: meta.color }}
                  >
                    <Icon size={10} />
                    {meta.label}
                  </span>
                  <ConfidencePill confidence={p.confidence} />
                </div>
                <button
                  onClick={() => setDrawerProv(p)}
                  className="block text-left w-full group"
                >
                  <p className="text-xs font-mono text-text-secondary group-hover:text-accent transition-colors truncate">
                    {p.source_file}
                  </p>
                  <p className="text-xs font-mono text-text-tertiary truncate">
                    {p.source_field}
                    {p.raw_value ? ` = "${String(p.raw_value).slice(0, 24)}"` : ""}
                  </p>
                </button>
                {p.extraction_method === "human" && p.extraction_model && (
                  <p className="text-xs font-mono" style={{ color: "#7c3aed" }}>{p.extraction_model}</p>
                )}
              </div>
            </div>
          </div>
        );
      })}
      {drawerProv && (
        <SourceRecordDrawer
          sourceFile={drawerProv.source_file}
          recordId={drawerProv.source_record_id}
          onClose={() => setDrawerProv(null)}
        />
      )}
    </div>
  );
}
