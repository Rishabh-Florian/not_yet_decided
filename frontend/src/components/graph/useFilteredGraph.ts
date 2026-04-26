"use client";
import { useMemo } from "react";
import { useFilterStore } from "@/store/filter-store";

export interface FilterableNode {
  id: string;
  type: string;
  name: string;
  timestamp?: number | null;
  source?: string | null;
}

export interface FilterableLink {
  source: string | { id: string };
  target: string | { id: string };
}

function endpointId(end: string | { id: string }): string {
  return typeof end === "string" ? end : end.id;
}

export interface FilteredGraph<N extends FilterableNode, L extends FilterableLink> {
  nodes: N[];
  links: L[];
  visibleIds: Set<string>;
  degrees: Map<string, number>;
  visibleCount: number;
  totalCount: number;
  availableSources: string[];
  maxDegree: number;
}

export function useFilteredGraph<N extends FilterableNode, L extends FilterableLink>(
  nodes: N[],
  links: L[]
): FilteredGraph<N, L> {
  const entityTypes = useFilterStore((s) => s.entityTypes);
  const timeWindowDays = useFilterStore((s) => s.timeWindowDays);
  const minConnections = useFilterStore((s) => s.minConnections);
  const sources = useFilterStore((s) => s.sources);
  const searchQuery = useFilterStore((s) => s.searchQuery);

  const degrees = useMemo(() => {
    const d = new Map<string, number>();
    for (const n of nodes) d.set(n.id, 0);
    for (const l of links) {
      const a = endpointId(l.source);
      const b = endpointId(l.target);
      d.set(a, (d.get(a) ?? 0) + 1);
      d.set(b, (d.get(b) ?? 0) + 1);
    }
    return d;
  }, [nodes, links]);

  const maxDegree = useMemo(() => {
    let m = 0;
    degrees.forEach((v) => {
      if (v > m) m = v;
    });
    return m;
  }, [degrees]);

  const availableSources = useMemo(() => {
    const set = new Set<string>();
    for (const n of nodes) if (n.source) set.add(n.source);
    return Array.from(set).sort();
  }, [nodes]);

  const cutoffMs = useMemo(() => {
    if (timeWindowDays == null) return null;
    return Date.now() - timeWindowDays * 86_400_000;
  }, [timeWindowDays]);

  const q = searchQuery.trim().toLowerCase();

  const visibleIds = useMemo(() => {
    const ids = new Set<string>();
    for (const n of nodes) {
      if (!entityTypes.has(n.type)) continue;
      if ((degrees.get(n.id) ?? 0) < minConnections) continue;
      if (sources && sources.size > 0 && (!n.source || !sources.has(n.source))) continue;
      if (cutoffMs != null && n.timestamp != null && n.timestamp < cutoffMs) continue;
      if (q && !n.name.toLowerCase().includes(q)) continue;
      ids.add(n.id);
    }
    return ids;
  }, [nodes, entityTypes, degrees, minConnections, sources, cutoffMs, q]);

  return {
    nodes,
    links,
    visibleIds,
    degrees,
    visibleCount: visibleIds.size,
    totalCount: nodes.length,
    availableSources,
    maxDegree,
  };
}
