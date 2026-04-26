"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { Conflict, ConflictListResponse } from "@/types/api";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
import { Button } from "@/components/ui/button";
import { CheckCircle, AlertTriangle, Clock, User, Cpu } from "lucide-react";
import Link from "next/link";

const VERDICT_LABEL: Record<string, string> = {
  LLM_TRIAGE: "LLM queued",
  ESCALATE: "Human needed",
};

const CONFIDENCE_RANK: Record<string, number> = {
  human: 4, exact: 3, grounded: 2, inferred: 1,
};

function confidenceColor(c: string) {
  if (c === "human") return "#9d9d9d";
  if (c === "exact") return "#e0e0e0";
  if (c === "grounded") return "#bdbdbd";
  return "#727272";
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const isEscalate = verdict === "ESCALATE";
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium ${
      isEscalate
        ? "bg-orange-500/10 text-orange-400"
        : "bg-blue-500/10 text-blue-400"
    }`}>
      {isEscalate ? <AlertTriangle size={10} /> : <Cpu size={10} />}
      {VERDICT_LABEL[verdict] ?? verdict}
    </span>
  );
}

function CandidateCard({ label, candidate, highlight }: {
  label: string;
  candidate: { value: unknown; confidence: string; source_file: string };
  highlight?: boolean;
}) {
  const color = confidenceColor(candidate.confidence);
  return (
    <div className={`flex-1 rounded-lg border p-3 space-y-1.5 ${highlight ? "border-accent/40 bg-accent/5" : "border-border-color bg-bg"}`}>
      <p className="text-xs font-medium text-text-tertiary uppercase tracking-wider">{label}</p>
      <p className="text-sm font-mono text-text-primary break-all">{String(candidate.value)}</p>
      <div className="flex items-center gap-2">
        <span className="text-xs px-1.5 py-0.5 rounded font-medium"
          style={{ background: color + "18", color }}>
          {candidate.confidence}
        </span>
        <span className="text-xs text-text-tertiary truncate font-mono">{candidate.source_file.split("/").pop()}</span>
      </div>
    </div>
  );
}

function ConflictRow({ conflict, onResolved }: { conflict: Conflict; onResolved: () => void }) {
  const qc = useQueryClient();
  const [chosenValue, setChosenValue] = useState<string>("");
  const [editor, setEditor] = useState<string>("");
  const [open, setOpen] = useState(false);

  const resolve = useMutation({
    mutationFn: async (value: unknown) => {
      const res = await fetch(`${BASE}/api/conflicts/${conflict.id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value, editor: editor || "human-ui" }),
      });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conflicts"] });
      onResolved();
    },
  });

  const pick = (side: "existing" | "incoming") => {
    const val = side === "existing" ? conflict.existing.value : conflict.incoming.value;
    resolve.mutate(val);
  };

  return (
    <div className="rounded-xl border border-border-color bg-bg-card overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-start gap-3 px-4 py-3 hover:bg-bg-hover transition-colors text-left"
      >
        <div className="mt-0.5">
          <AlertTriangle size={14} className="text-orange-400" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-0.5">
            <span className="text-sm font-medium text-text-primary">{conflict.attribute}</span>
            <VerdictBadge verdict={conflict.verdict} />
            <span className="text-xs text-text-tertiary font-mono">{conflict.reason}</span>
          </div>
          <div className="flex items-center gap-2 text-xs text-text-tertiary">
            <Link href={`/app/nodes/${encodeURIComponent(conflict.node_id)}`}
              onClick={e => e.stopPropagation()}
              className="hover:text-accent transition-colors font-mono truncate max-w-[200px]">
              {conflict.node_id}
            </Link>
            <span>·</span>
            <Clock size={10} />
            <span>{new Date(conflict.detected_at).toLocaleString()}</span>
          </div>
        </div>
        <span className="text-xs text-text-tertiary mt-0.5">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3 border-t border-border-color">
          <div className="flex gap-3 pt-3">
            <CandidateCard label="Existing" candidate={conflict.existing} />
            <CandidateCard label="Incoming" candidate={conflict.incoming} />
          </div>

          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline"
              disabled={resolve.isPending}
              onClick={() => pick("existing")}
              className="text-xs border-border-color text-text-secondary hover:text-text-primary">
              Keep existing
            </Button>
            <Button size="sm" variant="outline"
              disabled={resolve.isPending}
              onClick={() => pick("incoming")}
              className="text-xs border-accent/40 text-accent hover:bg-accent/10">
              Accept incoming
            </Button>
            <div className="flex-1" />
            <input
              value={editor}
              onChange={e => setEditor(e.target.value)}
              placeholder="your name (optional)"
              className="text-xs bg-bg border border-border-color rounded px-2 py-1 text-text-secondary w-36 focus:outline-none focus:border-accent/40"
            />
          </div>

          <div className="flex gap-2">
            <input
              value={chosenValue}
              onChange={e => setChosenValue(e.target.value)}
              placeholder="or type a custom value…"
              className="flex-1 text-xs bg-bg border border-border-color rounded px-2 py-1 text-text-secondary focus:outline-none focus:border-accent/40"
            />
            <Button size="sm" variant="outline"
              disabled={resolve.isPending || !chosenValue}
              onClick={() => resolve.mutate(chosenValue)}
              className="text-xs border-border-color text-text-secondary hover:text-text-primary">
              Set custom
            </Button>
          </div>

          {resolve.isError && (
            <p className="text-xs text-red-400">{String(resolve.error)}</p>
          )}
        </div>
      )}
    </div>
  );
}

export default function ConflictsPage() {
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const [resolved, setResolved] = useState(0);
  const qc = useQueryClient();

  const { data, isLoading, isError } = useQuery<ConflictListResponse>({
    queryKey: ["conflicts", status],
    queryFn: async () => {
      const res = await fetch(`${BASE}/api/conflicts?status=${status}&limit=100`);
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    refetchInterval: 10_000,
  });

  return (
    <div className="h-full overflow-auto p-6">
      <div className="max-w-3xl mx-auto space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">Conflict Inbox</h1>
            <p className="text-xs text-text-tertiary mt-0.5">
              Facts where two sources disagree — resolve them to update the graph.
            </p>
          </div>
          <div className="flex gap-1 rounded-lg border border-border-color p-0.5 bg-bg-card">
            {(["open", "resolved"] as const).map(s => (
              <button key={s} onClick={() => setStatus(s)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                  status === s ? "bg-accent-bg text-accent" : "text-text-secondary hover:text-text-primary"
                }`}>
                {s}
              </button>
            ))}
          </div>
        </div>

        {isLoading && (
          <div className="space-y-2">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="h-16 bg-bg-hover rounded-xl animate-pulse" />
            ))}
          </div>
        )}

        {isError && (
          <div className="rounded-xl border border-border-color bg-bg-card p-6 text-center">
            <p className="text-sm text-text-secondary">Could not load conflicts. Is the API running?</p>
          </div>
        )}

        {data && data.conflicts.length === 0 && (
          <div className="rounded-xl border border-border-color bg-bg-card p-10 text-center">
            <CheckCircle size={28} className="mx-auto mb-3 text-green-400/60" />
            <p className="text-sm text-text-primary font-medium">
              {status === "open" ? "No open conflicts" : "No resolved conflicts"}
            </p>
            <p className="text-xs text-text-tertiary mt-1">
              {status === "open" ? "All facts are in agreement." : "Resolve some conflicts to see them here."}
            </p>
          </div>
        )}

        {data && data.conflicts.map(c => (
          <ConflictRow
            key={c.id}
            conflict={c}
            onResolved={() => setResolved(r => r + 1)}
          />
        ))}

        {resolved > 0 && (
          <p className="text-xs text-green-400 text-center">
            {resolved} conflict{resolved !== 1 ? "s" : ""} resolved this session
          </p>
        )}
      </div>
    </div>
  );
}
