"use client";
import { useState, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { OnboardResponse } from "@/types/api";
import { Button } from "@/components/ui/button";
import { Upload, CheckCircle, ChevronDown, ChevronUp } from "lucide-react";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function OnboardPage() {
  const [tenant, setTenant] = useState("default");
  const [recordPath, setRecordPath] = useState("$[*]");
  const [sampleSize, setSampleSize] = useState(20);
  const [file, setFile] = useState<File | null>(null);
  const [specOpen, setSpecOpen] = useState(false);
  const [result, setResult] = useState<OnboardResponse | null>(null);
  const [promoteResult, setPromoteResult] = useState<OnboardResponse | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();

  const draft = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("No file selected");
      const fd = new FormData();
      fd.append("source_file", file);
      fd.append("tenant", tenant);
      fd.append("record_path", recordPath);
      fd.append("sample_size", String(sampleSize));

      const res = await fetch(`${BASE}/api/onboard`, {
        method: "POST",
        body: fd,
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`${res.status}: ${text}`);
      }
      return res.json() as Promise<OnboardResponse>;
    },
    onSuccess: (data) => {
      setResult(data);
      setSpecOpen(false);
    },
  });

  const promote = useMutation({
    mutationFn: async () => {
      if (!result?.spec_id) throw new Error("No spec_id to promote");
      const res = await fetch(`${BASE}/api/onboard/${result.spec_id}/promote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ editor: tenant }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`${res.status}: ${text}`);
      }
      return res.json() as Promise<OnboardResponse>;
    },
    onSuccess: (data) => {
      setPromoteResult(data);
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  return (
    <div className="h-full overflow-auto p-6">
      <div className="max-w-2xl mx-auto space-y-6">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">Add Data Source</h1>
          <p className="text-xs text-text-tertiary mt-0.5">
            Upload any JSON, JSONL, or CSV file — the LLM will draft a mapping spec for review.
          </p>
        </div>

        {!promoteResult && (
          <div className="rounded-xl border border-border-color bg-bg-card p-5 space-y-4">
            <div
              onClick={() => fileRef.current?.click()}
              className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-8 cursor-pointer transition-colors ${
                file ? "border-accent/40 bg-accent/5" : "border-border-color hover:border-accent/30 hover:bg-bg-hover"
              }`}
            >
              <Upload size={24} className={file ? "text-accent" : "text-text-tertiary"} />
              {file ? (
                <div className="text-center">
                  <p className="text-sm font-medium text-text-primary">{file.name}</p>
                  <p className="text-xs text-text-tertiary mt-0.5">{(file.size / 1024).toFixed(1)} KB</p>
                </div>
              ) : (
                <div className="text-center">
                  <p className="text-sm text-text-secondary">Drop a file or click to browse</p>
                  <p className="text-xs text-text-tertiary mt-0.5">JSON · JSONL · CSV</p>
                </div>
              )}
              <input
                ref={fileRef}
                type="file"
                accept=".json,.jsonl,.csv"
                className="hidden"
                onChange={e => setFile(e.target.files?.[0] ?? null)}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="space-y-1">
                <span className="text-xs text-text-tertiary font-medium">Tenant</span>
                <input
                  value={tenant}
                  onChange={e => setTenant(e.target.value)}
                  className="w-full text-sm bg-bg border border-border-color rounded px-2.5 py-1.5 text-text-primary focus:outline-none focus:border-accent/40"
                />
              </label>
              <label className="space-y-1">
                <span className="text-xs text-text-tertiary font-medium">Record path (JSONPath)</span>
                <input
                  value={recordPath}
                  onChange={e => setRecordPath(e.target.value)}
                  className="w-full text-sm bg-bg border border-border-color rounded px-2.5 py-1.5 text-text-primary focus:outline-none focus:border-accent/40"
                />
              </label>
            </div>

            <div className="flex items-center gap-3">
              <label className="space-y-1 w-32">
                <span className="text-xs text-text-tertiary font-medium">Sample size</span>
                <input
                  type="number"
                  value={sampleSize}
                  min={5}
                  max={100}
                  onChange={e => setSampleSize(Number(e.target.value))}
                  className="w-full text-sm bg-bg border border-border-color rounded px-2.5 py-1.5 text-text-primary focus:outline-none focus:border-accent/40"
                />
              </label>
              <div className="flex-1" />
              <Button
                disabled={!file || draft.isPending}
                onClick={() => draft.mutate()}
                className="mt-4"
              >
                {draft.isPending ? "Analyzing…" : "Draft spec with LLM"}
              </Button>
            </div>

            {draft.isError && (
              <p className="text-xs text-red-400 font-mono">{String(draft.error)}</p>
            )}
          </div>
        )}

        {result && !promoteResult && (
          <div className="rounded-xl border border-border-color bg-bg-card overflow-hidden">
            <div className="px-5 py-4 border-b border-border-color space-y-2">
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-1">
                  <p className="text-sm font-medium text-text-primary">Draft spec ready</p>
                  <p className="text-xs text-text-tertiary font-mono">{result.source_pattern}</p>
                </div>
                <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-500/10 text-yellow-400 font-medium">
                  draft v{result.spec_version}
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {result.node_types.map(t => (
                  <span key={t} className="text-xs px-2 py-0.5 rounded-md bg-bg-hover text-text-secondary font-mono">{t}</span>
                ))}
                {result.edge_types.map(t => (
                  <span key={t} className="text-xs px-2 py-0.5 rounded-md bg-accent/10 text-accent font-mono">{t}</span>
                ))}
              </div>
            </div>

            <button
              onClick={() => setSpecOpen(o => !o)}
              className="w-full flex items-center justify-between px-5 py-2.5 hover:bg-bg-hover transition-colors text-left"
            >
              <span className="text-xs font-medium text-text-tertiary uppercase tracking-wider">YAML spec</span>
              {specOpen ? <ChevronUp size={12} className="text-text-tertiary" /> : <ChevronDown size={12} className="text-text-tertiary" />}
            </button>
            {specOpen && (
              <pre className="px-5 py-3 text-xs font-mono text-text-secondary bg-bg overflow-auto max-h-64 border-t border-border-color">
                {result.yaml_text}
              </pre>
            )}

            <div className="px-5 py-4 border-t border-border-color flex gap-3">
              <Button
                variant="outline"
                onClick={() => { setResult(null); setFile(null); }}
                className="text-xs border-border-color text-text-secondary hover:text-text-primary"
              >
                Start over
              </Button>
              <Button
                onClick={() => promote.mutate()}
                disabled={promote.isPending || result.spec_id == null}
                className="text-xs"
              >
                {promote.isPending ? "Promoting…" : "Promote to active"}
              </Button>
              {result.spec_id == null && (
                <p className="text-xs text-orange-400 self-center">spec_id missing — cannot promote via API</p>
              )}
            </div>

            {promote.isError && (
              <p className="text-xs text-red-400 px-5 pb-3">{String(promote.error)}</p>
            )}
          </div>
        )}

        {promoteResult && (
          <div className="rounded-xl border border-accent/40 bg-accent/5 p-6 text-center space-y-2">
            <CheckCircle size={32} className="mx-auto text-accent" />
            <p className="text-sm font-medium text-text-primary">Spec activated</p>
            <p className="text-xs text-text-tertiary">
              <span className="font-mono text-accent">{promoteResult.source_pattern}</span> is now active (v{promoteResult.spec_version}).
              Push records via <span className="font-mono">POST /api/source/&lt;source_file&gt;/&lt;record_id&gt;</span>
            </p>
            <button
              onClick={() => { setResult(null); setPromoteResult(null); setFile(null); }}
              className="mt-2 text-xs text-text-tertiary hover:text-accent transition-colors"
            >
              Add another source
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
