"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useNodeDetail } from "@/hooks/useNodeDetail";
import { useEditNode } from "@/hooks/useEditNode";
import { nodeTypeColor, nodeDisplayName } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Save, CheckCircle } from "lucide-react";

export default function EditForm({ nodeId }: { nodeId: string }) {
  const router = useRouter();
  const { data: node, isLoading } = useNodeDetail(nodeId);
  const { mutate, isPending } = useEditNode(nodeId);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [editor, setEditor] = useState("");
  const [saved, setSaved] = useState(false);

  if (isLoading) {
    return (
      <div className="p-6 space-y-3 bg-bg">
        {[...Array(6)].map((_, i) => <div key={i} className="h-12 bg-bg-hover rounded-lg animate-pulse" />)}
      </div>
    );
  }
  if (!node) return <div className="p-6 text-text-secondary text-sm bg-bg">Node not found.</div>;

  const color = nodeTypeColor(node.type);

  const handleSave = () => {
    if (!editor.trim() || Object.keys(edits).length === 0) return;
    const attributes: Record<string, unknown> = {};
    Object.entries(edits).forEach(([k, v]) => { attributes[k] = v; });
    mutate({ attributes, editor }, {
      onSuccess: () => { setSaved(true); setTimeout(() => router.push(`/app/nodes/${encodeURIComponent(nodeId)}`), 1200); },
    });
  };

  return (
    <div className="max-w-xl mx-auto px-5 py-6 bg-bg min-h-full">
      <button onClick={() => router.back()}
        className="flex items-center gap-1 text-xs text-text-tertiary hover:text-accent mb-5 transition-colors">
        <ArrowLeft size={12} /> Back
      </button>

      <div className="flex items-center gap-2 mb-6">
        <span className="text-xs px-2 py-0.5 rounded-full font-medium"
          style={{ background: color + "18", color }}>{node.type}</span>
        <h1 className="text-base font-semibold text-text-primary">{nodeDisplayName(node)}</h1>
      </div>

      <div className="space-y-4">
        {Object.entries(node.attributes).map(([key, val]) => {
          const prov = node.provenance.find((p) => p.source_field?.includes(key));
          const currentVal = edits[key] !== undefined ? edits[key] : String(val);
          const isEdited = edits[key] !== undefined && edits[key] !== String(val);
          return (
            <div key={key} className="space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-xs font-mono text-text-secondary">{key}</label>
                {isEdited && <span className="text-xs font-medium" style={{ color: "#7c3aed" }}>edited</span>}
              </div>
              {String(val).length > 80 ? (
                <Textarea value={currentVal}
                  onChange={(e) => setEdits((p) => ({ ...p, [key]: e.target.value }))}
                  className="font-mono text-xs bg-bg-card border-border-color text-text-primary resize-none" rows={3} />
              ) : (
                <Input value={currentVal}
                  onChange={(e) => setEdits((p) => ({ ...p, [key]: e.target.value }))}
                  className="font-mono text-sm bg-bg-card border-border-color text-text-primary" />
              )}
              {prov && (
                <p className="text-xs text-text-tertiary">
                  From: {prov.extraction_method} · {prov.source_file}
                </p>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-8 pt-5 border-t border-border-color space-y-3">
        <div>
          <label className="text-xs font-mono text-text-secondary block mb-1">Your name / email *</label>
          <Input placeholder="you@company.com" value={editor}
            onChange={(e) => setEditor(e.target.value)}
            className="font-mono text-sm bg-bg-card border-border-color text-text-primary" />
          <p className="text-xs text-text-tertiary mt-1">Required for provenance tracking.</p>
        </div>

        {saved && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-green-50 text-green-700 text-sm border border-green-200">
            <CheckCircle size={14} />
            Changes saved with provenance tracking.
          </div>
        )}

        <Button onClick={handleSave}
          disabled={isPending || !editor.trim() || Object.keys(edits).length === 0}
          className="w-full bg-accent hover:bg-accent-dim text-white gap-2">
          <Save size={14} />
          {isPending ? "Saving…" : "Save changes"}
        </Button>
      </div>
    </div>
  );
}
