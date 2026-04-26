"use client";
import { useMemo } from "react";
import { useFilterStore } from "@/store/filter-store";

export interface FilterableNode {
  id: string;
  type: string;
  name: string;
  timestamp?: number | null;
  source?: string | null;
  department?: string | null;
  location?: string | null;
  orgSubtype?: string | null;
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
  availableDepartments: string[];
  availableLocations: string[];
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
  const subgraph = useFilterStore((s) => s.subgraph);
  const viewMode = useFilterStore((s) => s.viewMode);

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
    degrees.forEach((v) => { if (v > m) m = v; });
    return m;
  }, [degrees]);

  const availableSources = useMemo(() => {
    const s = new Set<string>();
    for (const n of nodes) if (n.source) s.add(n.source);
    return Array.from(s).sort();
  }, [nodes]);

  const availableDepartments = useMemo(() => {
    const s = new Set<string>();
    for (const n of nodes) if (n.department) s.add(n.department);
    return Array.from(s).sort();
  }, [nodes]);

  const availableLocations = useMemo(() => {
    const s = new Set<string>();
    for (const n of nodes) if (n.location) s.add(n.location);
    return Array.from(s).sort();
  }, [nodes]);

  const cutoffMs = useMemo(
    () => (timeWindowDays == null ? null : Date.now() - timeWindowDays * 86_400_000),
    [timeWindowDays]
  );

  const q = searchQuery.trim().toLowerCase();

  // Whether a node passes the standard (non-subgraph) filters.
  const passesBaseFilters = useMemo(() => {
    const result = new Set<string>();
    for (const n of nodes) {
      if (!entityTypes.has(n.type)) continue;
      if ((degrees.get(n.id) ?? 0) < minConnections) continue;
      if (sources && sources.size > 0 && (!n.source || !sources.has(n.source))) continue;
      if (cutoffMs != null && n.timestamp != null && n.timestamp < cutoffMs) continue;
      if (q && !n.name.toLowerCase().includes(q)) continue;
      result.add(n.id);
    }
    return result;
  }, [nodes, entityTypes, degrees, minConnections, sources, cutoffMs, q]);

  // Whether a node satisfies the subgraph filter (AND across dims, OR within each).
  // Only applied to Person nodes — Org nodes are included based on member visibility.
  const subgraphActive = (subgraph.departments != null && subgraph.departments.size > 0) ||
    (subgraph.locations != null && subgraph.locations.size > 0);

  const matchesSubgraph = useMemo(() => {
    if (!subgraphActive) return (_id: string) => true;

    const nodeById = new Map<string, N>();
    for (const n of nodes) nodeById.set(n.id, n);

    return (id: string): boolean => {
      const n = nodeById.get(id);
      if (!n) return false;

      // Organization nodes: always considered "matching" — their inclusion
      // is determined by whether any of their Person members are visible.
      if (n.type === "Organization") return true;

      if (n.type !== "Person") return true;

      if (subgraph.departments != null && subgraph.departments.size > 0) {
        if (!n.department || !subgraph.departments.has(n.department)) return false;
      }
      if (subgraph.locations != null && subgraph.locations.size > 0) {
        if (!n.location || !subgraph.locations.has(n.location)) return false;
      }
      return true;
    };
  }, [nodes, subgraph, subgraphActive]);

  // Build adjacency for expand mode.
  const adjacency = useMemo(() => {
    if (!subgraphActive || viewMode !== "expand") return null;
    const adj = new Map<string, Set<string>>();
    for (const l of links) {
      const a = endpointId(l.source);
      const b = endpointId(l.target);
      if (!adj.has(a)) adj.set(a, new Set());
      if (!adj.has(b)) adj.set(b, new Set());
      adj.get(a)!.add(b);
      adj.get(b)!.add(a);
    }
    return adj;
  }, [links, subgraphActive, viewMode]);

  const visibleIds = useMemo(() => {
    const ids = new Set<string>();

    if (!subgraphActive) {
      // No subgraph filter: show all nodes passing base filters.
      passesBaseFilters.forEach((id) => ids.add(id));
      return ids;
    }

    // Determine which Person nodes are "seed" nodes (match subgraph AND base).
    const seedIds = new Set<string>();
    passesBaseFilters.forEach((id) => {
      if (matchesSubgraph(id)) seedIds.add(id);
    });

    if (viewMode === "isolate") {
      seedIds.forEach((id) => ids.add(id));
      // Include Organization nodes linked to at least one seed Person.
      for (const l of links) {
        const a = endpointId(l.source);
        const b = endpointId(l.target);
        if (seedIds.has(a) && seedIds.has(b)) {
          ids.add(a);
          ids.add(b);
        }
      }
      // Also add Org nodes that have a MEMBER_OF edge to any seed.
      for (const l of links) {
        const a = endpointId(l.source);
        const b = endpointId(l.target);
        if (seedIds.has(a)) {
          const bNode = nodes.find((n) => n.id === b);
          if (bNode?.type === "Organization") ids.add(b);
        }
        if (seedIds.has(b)) {
          const aNode = nodes.find((n) => n.id === a);
          if (aNode?.type === "Organization") ids.add(a);
        }
      }
    } else if (viewMode === "expand") {
      // Seed nodes + their direct neighbors.
      seedIds.forEach((id) => ids.add(id));
      if (adjacency) {
        seedIds.forEach((id) => {
          const neighbors = adjacency.get(id);
          if (neighbors) {
            neighbors.forEach((nb) => {
              if (passesBaseFilters.has(nb)) ids.add(nb);
            });
          }
        });
      }
    } else {
      // dim mode: all base-filtered nodes are "visible" (opacity handled by GraphView).
      passesBaseFilters.forEach((id) => ids.add(id));
    }

    return ids;
  }, [passesBaseFilters, matchesSubgraph, subgraphActive, viewMode, adjacency, links, nodes]);

  // For dim mode, the "highlighted" ids are the seed nodes (full opacity).
  // GraphView uses visibleIds for opacity, so we expose matchedIds separately.
  const matchedIds = useMemo(() => {
    if (!subgraphActive || viewMode !== "dim") return visibleIds;
    const ids = new Set<string>();
    passesBaseFilters.forEach((id) => {
      if (matchesSubgraph(id)) ids.add(id);
    });
    return ids;
  }, [passesBaseFilters, matchesSubgraph, subgraphActive, viewMode, visibleIds]);

  return {
    nodes,
    links,
    visibleIds: viewMode === "dim" && subgraphActive ? matchedIds : visibleIds,
    degrees,
    visibleCount: (viewMode === "dim" && subgraphActive ? matchedIds : visibleIds).size,
    totalCount: nodes.length,
    availableSources,
    availableDepartments,
    availableLocations,
    maxDegree,
  };
}
