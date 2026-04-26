"use client";
import { useQuery } from "@tanstack/react-query";
import { apiPost } from "@/lib/api-client";
import { nodeTypeColor, nodeDisplayName } from "@/lib/utils";
import type { PatternQueryResponse } from "@/types/api";
import { useAppStore } from "@/store/app-store";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import { useMemo, useRef, useState, useCallback } from "react";

const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), { ssr: false });

interface GNode {
  id: string;
  type: string;
  name: string;
  val: number;
  color: string;
}

interface GLink {
  source: string;
  target: string;
  relation: string;
}

interface GraphData {
  nodes: GNode[];
  links: GLink[];
}

async function fetchCommunicationGraph(): Promise<GraphData> {
  // Fetch sender→message edges and message→receiver edges in parallel
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

  function addNode(raw: { id: string; type: string; attributes: Record<string, unknown>; provenance: unknown[] }) {
    if (nodeMap.has(raw.id)) return;
    nodeMap.set(raw.id, {
      id: raw.id,
      type: raw.type,
      name: nodeDisplayName(raw as Parameters<typeof nodeDisplayName>[0]),
      val: raw.type === "Person" ? 6 : 2,
      color: nodeTypeColor(raw.type),
    });
  }

  function addLink(sourceId: string, targetId: string, relation: string) {
    const key = `${sourceId}→${targetId}`;
    if (!linkMap.has(key)) {
      linkMap.set(key, { source: sourceId, target: targetId, relation });
    }
  }

  for (const m of sentResult.matches) {
    addNode(m.source);
    addNode(m.target);
    addLink(m.source.id, m.target.id, m.edge.relation_type);
  }

  for (const m of receivedResult.matches) {
    // Only include receiver persons that we already have messages for
    if (!nodeMap.has(m.source.id)) continue;
    addNode(m.target);
    addLink(m.source.id, m.target.id, m.edge.relation_type);
  }

  return {
    nodes: Array.from(nodeMap.values()),
    links: Array.from(linkMap.values()),
  };
}

function useGraphData() {
  return useQuery({
    queryKey: ["graph-communication"],
    queryFn: fetchCommunicationGraph,
    staleTime: 120_000,
  });
}

const NODE_TYPE_LABELS = [
  { type: "Person", color: nodeTypeColor("Person") },
  { type: "Message", color: nodeTypeColor("Message") },
];

export default function GraphView() {
  const { data, isLoading, isError } = useGraphData();
  const { setSelectedNodeId } = useAppStore();
  const router = useRouter();
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const graphRef = useRef<{ zoomToFit?: (ms: number) => void } | null>(null);

  const graphData = useMemo<GraphData>(() => data ?? { nodes: [], links: [] }, [data]);

  const handleNodeClick = useCallback(
    (node: object) => {
      const n = node as GNode;
      setSelectedNodeId(n.id);
      router.push(`/app/nodes/${encodeURIComponent(n.id)}`);
    },
    [setSelectedNodeId, router]
  );

  const paintNode = useCallback(
    (node: object, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const n = node as GNode & { x: number; y: number };
      const r = Math.sqrt(n.val) * 2.8;
      const isHov = n.id === hoveredId;

      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
      ctx.fillStyle = isHov ? n.color : n.color + "cc";
      ctx.fill();

      if (isHov) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 3, 0, 2 * Math.PI);
        ctx.strokeStyle = n.color + "55";
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      if (globalScale > 1.5 && n.type === "Person") {
        const label = n.name.length > 16 ? n.name.slice(0, 15) + "…" : n.name;
        const fontSize = Math.max(8, 10 / globalScale);
        ctx.font = `${fontSize}px system-ui`;
        ctx.fillStyle = "#bcbcbc";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(label, n.x, n.y + r + 2);
      }
    },
    [hoveredId]
  );

  if (isError) {
    return (
      <div className="h-full flex items-center justify-center bg-bg">
        <div className="text-center space-y-2">
          <p className="text-sm text-text-secondary font-medium">Backend not reachable</p>
          <p className="text-xs text-text-tertiary">Start the API server at localhost:8000</p>
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="h-full flex items-center justify-center bg-bg">
        <div className="text-center space-y-3">
          <div className="w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-xs text-text-secondary">Loading communication graph…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-full w-full bg-bg overflow-hidden">
      <ForceGraph2D
        ref={graphRef as never}
        graphData={graphData}
        backgroundColor="#080808"
        nodeColor={(n) => (n as GNode).color}
        nodeVal={(n) => (n as GNode).val}
        nodeLabel={(n) => {
          const node = n as GNode;
          return `${node.type}: ${node.name}`;
        }}
        linkColor={() => "#3f3f3f"}
        linkWidth={0.6}
        linkDirectionalArrowLength={2.5}
        linkDirectionalArrowRelPos={1}
        linkDirectionalParticles={2}
        linkDirectionalParticleWidth={1.2}
        linkDirectionalParticleColor={() => "#a0a0a0"}
        onNodeClick={handleNodeClick}
        onNodeHover={(node) => setHoveredId(node ? (node as GNode).id : null)}
        nodeCanvasObject={paintNode}
        nodeCanvasObjectMode={() => "replace"}
        cooldownTicks={120}
        onEngineStop={() => graphRef.current?.zoomToFit?.(600)}
        width={undefined}
        height={undefined}
      />

      {/* Legend */}
      <div className="absolute top-3 left-3 rounded-xl border border-border-color bg-black/75 p-2.5 shadow-sm backdrop-blur-sm space-y-1">
        {NODE_TYPE_LABELS.map(({ type, color }) => (
          <span key={type} className="flex items-center gap-1.5 text-xs text-text-secondary">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: color }} />
            {type}
          </span>
        ))}
      </div>

      {/* Stats overlay */}
      <div className="absolute top-3 right-3 rounded-lg border border-border-color bg-black/75 px-2.5 py-1.5 text-xs text-text-tertiary font-mono shadow-sm backdrop-blur-sm">
        {graphData.nodes.filter((n) => n.type === "Person").length} people ·{" "}
        {graphData.nodes.filter((n) => n.type === "Message").length} messages ·{" "}
        {graphData.links.length} edges
      </div>

      {/* Reset zoom */}
      <div className="absolute bottom-3 right-3">
        <button
          onClick={() => graphRef.current?.zoomToFit?.(400)}
          className="rounded-lg border border-border-color bg-black/80 px-3 py-1.5 text-xs text-text-secondary shadow-sm transition-colors hover:border-accent/30 hover:text-accent"
        >
          Reset zoom
        </button>
      </div>
    </div>
  );
}
