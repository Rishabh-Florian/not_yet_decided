"use client";
import { useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { useNodeList } from "@/hooks/useNodeList";
import { useAppStore } from "@/store/app-store";
import { nodeTypeColor, nodeDisplayName } from "@/lib/utils";
import ConfidencePill from "@/components/provenance/ConfidencePill";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Eye, Pencil, ChevronDown } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import type { NodeResponse } from "@/types/api";

export default function NodeListTable({ type }: { type: string }) {
  const router = useRouter();
  const { setSelectedNodeId } = useAppStore();
  const [filter, setFilter] = useState("");
  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useNodeList(type, 100);

  const allNodes = useMemo(() => data?.pages.flatMap((p) => p.nodes) ?? [], [data]);
  const filtered = useMemo(() => {
    if (!filter) return allNodes;
    const q = filter.toLowerCase();
    return allNodes.filter((n) => nodeDisplayName(n).toLowerCase().includes(q) || n.id.toLowerCase().includes(q));
  }, [allNodes, filter]);

  const color = nodeTypeColor(type);

  return (
    <div className="h-full flex flex-col bg-bg">
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border-color bg-bg-card">
        <div className="w-2 h-2 rounded-full shrink-0" style={{ background: color }} />
        <span className="text-sm font-semibold text-text-primary">{type}</span>
        <span className="text-xs text-text-tertiary font-mono">
          {data?.pages[0]?.total.toLocaleString() ?? "—"} total
        </span>
        <Input
          placeholder="Filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="ml-auto w-44 h-7 text-xs bg-bg border-border-color text-text-primary placeholder:text-text-tertiary"
        />
      </div>

      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="p-4 space-y-2">
            {[...Array(10)].map((_, i) => (
              <div key={i} className="h-9 rounded-lg bg-bg-hover animate-pulse" />
            ))}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-bg-card z-10 border-b border-border-color">
              <tr>
                {["Name", "ID", "Confidence", "Sources", "Updated", ""].map((h) => (
                  <th key={h} className="px-4 py-2 text-left text-xs text-text-tertiary font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-xs text-text-tertiary">No results.</td></tr>
              ) : filtered.map((node: NodeResponse) => {
                const sources = new Set(node.provenance.map((p) => p.source_file)).size;
                return (
                  <tr
                    key={node.id}
                    className="border-b border-border-color-subtle hover:bg-bg-hover transition-colors cursor-pointer group"
                    onClick={() => { setSelectedNodeId(node.id); router.push(`/app/nodes/${encodeURIComponent(node.id)}`); }}
                  >
                    <td className="px-4 py-2.5 text-text-primary font-medium max-w-[200px] truncate">
                      {nodeDisplayName(node)}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-text-tertiary max-w-[160px] truncate">
                      {node.id}
                    </td>
                    <td className="px-4 py-2.5"><ConfidencePill confidence={node.confidence} /></td>
                    <td className="px-4 py-2.5 text-text-secondary text-xs">{sources}</td>
                    <td className="px-4 py-2.5 text-text-tertiary text-xs">
                      {node.updated_at ? formatDistanceToNow(new Date(node.updated_at), { addSuffix: true }) : "—"}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>
                        <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-text-secondary hover:text-accent"
                          onClick={() => { setSelectedNodeId(node.id); router.push(`/app/nodes/${encodeURIComponent(node.id)}`); }}>
                          <Eye size={12} />
                        </Button>
                        <Button size="sm" variant="ghost" className="h-6 w-6 p-0 text-text-secondary hover:text-accent"
                          onClick={() => router.push(`/app/edit/${encodeURIComponent(node.id)}`)}>
                          <Pencil size={12} />
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {hasNextPage && (
          <div className="flex justify-center py-3">
            <Button variant="ghost" size="sm" onClick={() => fetchNextPage()} disabled={isFetchingNextPage}
              className="text-xs text-text-secondary gap-1">
              <ChevronDown size={12} />
              {isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
