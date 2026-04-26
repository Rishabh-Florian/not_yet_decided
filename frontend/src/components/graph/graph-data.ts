"use client";
import { useQuery } from "@tanstack/react-query";
import { apiPost } from "@/lib/api-client";
import { nodeDisplayName } from "@/lib/utils";
import type { PatternQueryResponse } from "@/types/api";

export const PERSON_COLOR = "#E8E8E5";
export const MESSAGE_COLOR = "#6B7280";
export const PERSON_SIZE = 6;
export const MESSAGE_SIZE = 3;
export const HUB_DEGREE_THRESHOLD = 12;

export interface GNode {
  id: string;
  type: string;
  name: string;
  val: number;
  color: string;
  degree: number;
  timestamp: number | null;
  source: string | null;
}

export interface GLink {
  source: string;
  target: string;
  relation: string;
  weight: number;
}

export interface GraphData {
  nodes: GNode[];
  links: GLink[];
}

type RawNode = {
  id: string;
  type: string;
  attributes: Record<string, unknown>;
  provenance?: Array<{ source_file?: string }>;
};

function deriveSource(raw: RawNode): string | null {
  const file = raw.provenance?.[0]?.source_file;
  if (!file) return null;
  const head = file.split("/")[0] ?? file;
  return head.replace(/_/g, " ");
}

function deriveTimestamp(raw: RawNode): number | null {
  const a = raw.attributes;
  const candidates = [a.sent_at, a.created_at, a.timestamp, a.date, a.occurred_at];
  for (const c of candidates) {
    if (typeof c === "string") {
      const t = Date.parse(c);
      if (!Number.isNaN(t)) return t;
    } else if (typeof c === "number") {
      return c;
    }
  }
  return null;
}

async function fetchCommunicationGraph(): Promise<GraphData> {
  const [sentResult, receivedResult] = await Promise.all([
    apiPost<PatternQueryResponse>("/api/graph/query", {
      pattern: "(Person)-[SENT]->(Message)",
      limit: 500,
    }),
    apiPost<PatternQueryResponse>("/api/graph/query", {
      pattern: "(Message)-[RECEIVED]->(Person)",
      limit: 2000,
    }),
  ]);

  const nodeMap = new Map<string, GNode>();
  const linkMap = new Map<string, GLink>();

  function addNode(raw: RawNode) {
    if (nodeMap.has(raw.id)) return;
    const isPerson = raw.type === "Person";
    nodeMap.set(raw.id, {
      id: raw.id,
      type: raw.type,
      name: nodeDisplayName(raw),
      val: isPerson ? PERSON_SIZE : MESSAGE_SIZE,
      color: isPerson ? PERSON_COLOR : MESSAGE_COLOR,
      degree: 0,
      timestamp: deriveTimestamp(raw),
      source: deriveSource(raw),
    });
  }

  function addLink(sourceId: string, targetId: string, relation: string) {
    const key = `${sourceId}→${targetId}`;
    if (!linkMap.has(key)) {
      linkMap.set(key, { source: sourceId, target: targetId, relation, weight: 1 });
    }
  }

  for (const m of sentResult.matches) {
    addNode(m.source as RawNode);
    addNode(m.target as RawNode);
    addLink(m.source.id, m.target.id, m.edge.relation_type);
  }

  for (const m of receivedResult.matches) {
    if (!nodeMap.has(m.source.id)) continue;
    addNode(m.target as RawNode);
    addLink(m.source.id, m.target.id, m.edge.relation_type);
  }

  linkMap.forEach((link) => {
    const s = nodeMap.get(link.source);
    const t = nodeMap.get(link.target);
    if (s) s.degree += 1;
    if (t) t.degree += 1;
  });
  linkMap.forEach((link) => {
    const s = nodeMap.get(link.source);
    const t = nodeMap.get(link.target);
    link.weight = Math.max(s?.degree ?? 0, t?.degree ?? 0);
  });

  return {
    nodes: Array.from(nodeMap.values()),
    links: Array.from(linkMap.values()),
  };
}

export function useGraphData() {
  return useQuery({
    queryKey: ["graph-communication"],
    queryFn: fetchCommunicationGraph,
    staleTime: 120_000,
  });
}
