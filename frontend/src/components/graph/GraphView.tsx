"use client";
import { useAppStore } from "@/store/app-store";
import { useFilterStore } from "@/store/filter-store";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  HUB_DEGREE_THRESHOLD,
  MESSAGE_COLOR,
  MESSAGE_SIZE,
  PERSON_COLOR,
  PERSON_SIZE,
  useGraphData,
  type GLink,
  type GNode,
} from "./graph-data";
import { useFilteredGraph } from "./useFilteredGraph";

const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), { ssr: false });

const NODE_TYPE_LABELS = [
  { type: "Person", color: PERSON_COLOR, size: PERSON_SIZE },
  { type: "Message", color: MESSAGE_COLOR, size: MESSAGE_SIZE },
];

const DIM_NODE_OPACITY = 0.05;
const DIM_LINK_OPACITY = 0.02;
const TRANSITION_MS = 600;
const AUTO_FIT_THRESHOLD = 50;

// Cubic-bezier(0.16, 1, 0.3, 1), Newton-solved on x.
function makeBezier(x1: number, y1: number, x2: number, y2: number) {
  const cx = 3 * x1;
  const bx = 3 * (x2 - x1) - cx;
  const ax = 1 - cx - bx;
  const cy = 3 * y1;
  const by = 3 * (y2 - y1) - cy;
  const ay = 1 - cy - by;
  const sx = (t: number) => ((ax * t + bx) * t + cx) * t;
  const sdx = (t: number) => (3 * ax * t + 2 * bx) * t + cx;
  const sy = (t: number) => ((ay * t + by) * t + cy) * t;
  return (x: number) => {
    let t = x;
    for (let i = 0; i < 6; i++) {
      const dx = sx(t) - x;
      if (Math.abs(dx) < 1e-4) break;
      const d = sdx(t);
      if (Math.abs(d) < 1e-6) break;
      t -= dx / d;
    }
    return sy(t);
  };
}
const easeBezier = makeBezier(0.16, 1, 0.3, 1);

interface GraphRef {
  zoomToFit?: (durationMs: number, padding?: number, nodeFilter?: (n: object) => boolean) => void;
  refresh?: () => void;
}

export default function GraphView() {
  const { data, isLoading, isError } = useGraphData();
  const { setSelectedNodeId } = useAppStore();
  const reset = useFilterStore((s) => s.reset);
  const router = useRouter();
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const graphRef = useRef<GraphRef | null>(null);

  const graphData = useMemo(() => data ?? { nodes: [], links: [] }, [data]);
  const filtered = useFilteredGraph<GNode, GLink>(graphData.nodes, graphData.links);
  const { visibleIds, visibleCount, totalCount } = filtered;

  const maxWeight = useMemo(
    () => graphData.links.reduce((m, l) => Math.max(m, l.weight), 1),
    [graphData.links]
  );

  // Initial fit after layout settles (idle physics never truly stop).
  useEffect(() => {
    if (!data) return;
    const t = setTimeout(() => graphRef.current?.zoomToFit?.(800), 1200);
    return () => clearTimeout(t);
  }, [data]);

  // --- Per-node opacity animation (filter dim/undim) ---
  const opacityRef = useRef<Map<string, number>>(new Map());
  const animFromRef = useRef<Map<string, number>>(new Map());
  const animTargetRef = useRef<Map<string, number>>(new Map());
  const animStartRef = useRef<number>(0);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const targets = animTargetRef.current;
    const from = animFromRef.current;
    const current = opacityRef.current;
    let changed = false;
    for (const n of graphData.nodes) {
      if (!current.has(n.id)) {
        current.set(n.id, visibleIds.has(n.id) ? 1 : DIM_NODE_OPACITY);
      }
    }
    for (const n of graphData.nodes) {
      const t = visibleIds.has(n.id) ? 1 : DIM_NODE_OPACITY;
      if (targets.get(n.id) !== t) {
        targets.set(n.id, t);
        from.set(n.id, current.get(n.id) ?? t);
        changed = true;
      }
    }
    if (!changed) return;
    animStartRef.current = performance.now();
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    const tick = (now: number) => {
      const elapsed = now - animStartRef.current;
      const p = Math.min(1, elapsed / TRANSITION_MS);
      const eased = easeBezier(p);
      targets.forEach((target, id) => {
        const start = from.get(id) ?? target;
        current.set(id, start + (target - start) * eased);
      });
      graphRef.current?.refresh?.();
      if (p < 1) rafRef.current = requestAnimationFrame(tick);
      else rafRef.current = null;
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [graphData.nodes, visibleIds]);

  // Auto-fit visible bounds when filter narrows below threshold.
  useEffect(() => {
    if (visibleCount === 0 || visibleCount > AUTO_FIT_THRESHOLD) return;
    const id = window.setTimeout(() => {
      graphRef.current?.zoomToFit?.(TRANSITION_MS, 80, (n) => visibleIds.has((n as GNode).id));
    }, TRANSITION_MS + 30);
    return () => window.clearTimeout(id);
  }, [visibleCount, visibleIds]);

  const handleNodeClick = useCallback(
    (node: object) => {
      const n = node as GNode;
      if (!visibleIds.has(n.id)) return;
      setSelectedNodeId(n.id);
      router.push(`/app/nodes/${encodeURIComponent(n.id)}`);
    },
    [setSelectedNodeId, router, visibleIds]
  );

  const paintNode = useCallback(
    (node: object, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const n = node as GNode & { x: number; y: number };
      const r = Math.sqrt(n.val) * 2.4;
      const isHov = n.id === hoveredId;
      const isHub = n.type === "Person" && n.degree >= HUB_DEGREE_THRESHOLD;
      const op = opacityRef.current.get(n.id) ?? 1;

      ctx.save();
      ctx.globalAlpha = op;

      if ((isHub || isHov) && op > 0.4) {
        ctx.save();
        ctx.shadowColor = "rgba(255,255,255,0.55)";
        ctx.shadowBlur = isHov ? 18 : 14;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
        ctx.fillStyle = n.color;
        ctx.globalAlpha = 0.18 * op;
        ctx.fill();
        ctx.restore();
      }

      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
      ctx.fillStyle = isHov ? n.color : n.color + (n.type === "Person" ? "" : "cc");
      ctx.fill();

      if (isHov && op > 0.4) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 3, 0, 2 * Math.PI);
        ctx.strokeStyle = "rgba(232,232,229,0.45)";
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }

      if (globalScale > 1.5 && n.type === "Person" && op > 0.4) {
        const label = n.name.length > 16 ? n.name.slice(0, 15) + "…" : n.name;
        const fontSize = Math.max(8, 10 / globalScale);
        ctx.font = `${fontSize}px system-ui`;
        ctx.fillStyle = "#bcbcbc";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(label, n.x, n.y + r + 2);
      }

      ctx.restore();
    },
    [hoveredId]
  );

  const linkVisible = useCallback(
    (l: GLink) => {
      const a = typeof l.source === "string" ? l.source : (l.source as { id: string }).id;
      const b = typeof l.target === "string" ? l.target : (l.target as { id: string }).id;
      return visibleIds.has(a) && visibleIds.has(b);
    },
    [visibleIds]
  );

  const linkColor = useCallback(
    (link: object) => {
      const l = link as GLink;
      if (!linkVisible(l)) return `rgba(220,220,220,${DIM_LINK_OPACITY})`;
      const t = Math.min(1, l.weight / Math.max(maxWeight * 0.5, 1));
      const alpha = 0.08 + t * 0.32;
      return `rgba(220,220,220,${alpha.toFixed(3)})`;
    },
    [maxWeight, linkVisible]
  );

  const particleColor = useCallback(
    (link: object) => {
      const l = link as GLink;
      if (!linkVisible(l)) return "rgba(200,200,200,0.02)";
      const t = Math.min(1, l.weight / Math.max(maxWeight * 0.5, 1));
      const alpha = 0.25 + t * 0.45;
      return `rgba(200,200,200,${alpha.toFixed(3)})`;
    },
    [maxWeight, linkVisible]
  );

  if (isError) {
    return (
      <div className="flex h-full items-center justify-center bg-bg">
        <div className="space-y-2 text-center">
          <p className="text-sm font-medium text-text-secondary">Backend not reachable</p>
          <p className="text-xs text-text-tertiary">Start the API server at localhost:8000</p>
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center bg-bg">
        <div className="space-y-3 text-center">
          <div className="mx-auto h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          <p className="text-xs text-text-secondary">Loading communication graph…</p>
        </div>
      </div>
    );
  }

  const personCount = graphData.nodes.filter((n) => n.type === "Person").length;
  const messageCount = graphData.nodes.filter((n) => n.type === "Message").length;
  const showEmpty = totalCount > 0 && visibleCount === 0;

  return (
    <div
      className="relative h-full w-full overflow-hidden"
      style={{
        background: "radial-gradient(ellipse at center, #0A0A0B 0%, #050506 60%, #000 100%)",
      }}
    >
      <ForceGraph2D
        ref={graphRef as never}
        graphData={graphData}
        backgroundColor="rgba(0,0,0,0)"
        nodeColor={(n) => (n as GNode).color}
        nodeVal={(n) => (n as GNode).val}
        nodeLabel={(n) => {
          const node = n as GNode;
          return `${node.type}: ${node.name}`;
        }}
        linkColor={linkColor}
        linkWidth={(l) => 0.4 + Math.min(0.8, ((l as GLink).weight / Math.max(maxWeight, 1)) * 0.8)}
        linkDirectionalArrowLength={2}
        linkDirectionalArrowRelPos={1}
        linkDirectionalParticles={(l) => (linkVisible(l as GLink) ? 1 : 0)}
        linkDirectionalParticleWidth={1}
        linkDirectionalParticleColor={particleColor}
        onNodeClick={handleNodeClick}
        onNodeHover={(node) => setHoveredId(node ? (node as GNode).id : null)}
        nodeCanvasObject={paintNode}
        nodeCanvasObjectMode={() => "replace"}
        warmupTicks={80}
        cooldownTicks={Infinity}
        cooldownTime={Infinity}
        d3AlphaDecay={0.003}
        d3VelocityDecay={0.85}
        d3AlphaMin={0.002}
        width={undefined}
        height={undefined}
      />

      {/* Legend */}
      <div className="absolute left-3 top-3 space-y-1.5 rounded-xl border border-border-color bg-black/75 p-2.5 shadow-sm backdrop-blur-sm">
        {NODE_TYPE_LABELS.map(({ type, color, size }) => (
          <span key={type} className="flex items-center gap-2 text-xs text-text-secondary">
            <span
              className="shrink-0 rounded-full"
              style={{ background: color, width: size + 2, height: size + 2 }}
            />
            {type}
          </span>
        ))}
      </div>

      {/* Stats overlay */}
      <div className="absolute right-3 top-3 rounded-lg border border-border-color bg-black/75 px-2.5 py-1.5 font-mono text-xs text-text-tertiary shadow-sm backdrop-blur-sm">
        {personCount} people · {messageCount} messages · {graphData.links.length} edges
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

      {/* Empty state */}
      {showEmpty && (
        <div
          role="status"
          aria-live="polite"
          className="pointer-events-none absolute inset-0 flex items-center justify-center"
        >
          <div className="pointer-events-auto rounded-xl border border-border-color bg-black/85 px-6 py-5 text-center shadow-lg backdrop-blur-sm">
            <p className="text-sm text-text-primary">No nodes match current filters</p>
            <button
              onClick={reset}
              className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border-color px-3 py-1.5 font-mono text-xs text-text-secondary transition-colors hover:border-accent/40 hover:text-accent"
            >
              Reset filters
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
