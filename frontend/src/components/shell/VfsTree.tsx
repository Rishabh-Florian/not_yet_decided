"use client";
import { useGraphStats } from "@/hooks/useGraphStats";
import { NODE_TYPE_COLORS, NODE_TYPES, NODE_TYPE_VFS } from "@/lib/utils";
import { useFilterStore } from "@/store/filter-store";
import { Folder, FolderOpen, Database } from "lucide-react";
import Link from "next/link";
import { useSearchParams, usePathname } from "next/navigation";
import { useState } from "react";

export default function VfsTree() {
  const { data: stats } = useGraphStats();
  const [expanded, setExpanded] = useState(true);
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const currentType = searchParams.get("type");

  const entityTypes = useFilterStore((s) => s.entityTypes);
  const setEntityTypesExclusive = useFilterStore((s) => s.setEntityTypesExclusive);
  const onGraph = pathname?.startsWith("/app/graph") ?? false;

  return (
    <div className="flex flex-col flex-none bg-bg overflow-y-auto select-none">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex items-center gap-2 px-3 py-2.5 text-xs font-medium text-text-tertiary uppercase tracking-widest hover:text-text-secondary transition-colors border-b border-border-color-subtle"
      >
        <Database size={11} className="text-accent" />
        <span>Company Graph</span>
      </button>

      {expanded && (
        <div className="py-1">
          {NODE_TYPES.map((type) => {
            const count = stats?.graph?.node_types?.[type] ?? 0;
            const vfsName = NODE_TYPE_VFS[type];
            const color = NODE_TYPE_COLORS[type];

            // Active highlight: while on /app/graph reflect the filter store
            // (single-type exclusive selection). Elsewhere fall back to the
            // existing /app/nodes?type=X behavior.
            const isFilterActive =
              onGraph && entityTypes.size === 1 && entityTypes.has(type);
            const isNodesActive =
              !onGraph && pathname.startsWith("/app/nodes") && currentType === type;
            const isActive = isFilterActive || isNodesActive;

            const className = `flex items-center gap-2 mx-1.5 px-2 py-1.5 text-sm rounded-md transition-colors ${
              isActive
                ? "bg-accent-bg text-accent font-medium"
                : "text-text-secondary hover:bg-bg-hover hover:text-text-primary"
            }`;

            const inner = (
              <>
                {isActive ? (
                  <FolderOpen size={13} style={{ color }} className="shrink-0" />
                ) : (
                  <Folder size={13} style={{ color }} className="shrink-0" />
                )}
                <span className="flex-1 font-mono text-xs truncate">/{vfsName}</span>
                {count > 0 && (
                  <span
                    className="text-xs px-1.5 py-0.5 rounded-full font-mono shrink-0 tabular-nums"
                    style={{ background: color + "18", color }}
                  >
                    {count > 9999 ? (count / 1000).toFixed(0) + "k" : count.toLocaleString()}
                  </span>
                )}
              </>
            );

            // On graph page, folder click toggles entity-type filter (exclusive,
            // click-again-to-clear). Elsewhere keep nav to /app/nodes?type=X.
            if (onGraph) {
              return (
                <button
                  key={type}
                  type="button"
                  onClick={() => setEntityTypesExclusive(type)}
                  aria-pressed={isFilterActive}
                  aria-label={`Filter graph to ${type} nodes`}
                  className={`${className} text-left w-[calc(100%-0.75rem)]`}
                >
                  {inner}
                </button>
              );
            }

            return (
              <Link key={type} href={`/app/nodes?type=${type}`} className={className}>
                {inner}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
