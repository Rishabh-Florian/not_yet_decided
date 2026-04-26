"use client";
import { useGraphStats } from "@/hooks/useGraphStats";
import { NODE_TYPE_COLORS, NODE_TYPES, NODE_TYPE_VFS } from "@/lib/utils";
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

  return (
    <div className="flex flex-col h-full bg-bg overflow-y-auto select-none">
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
            const isActive = pathname.startsWith("/app/nodes") && currentType === type;

            return (
              <Link
                key={type}
                href={`/app/nodes?type=${type}`}
                className={`flex items-center gap-2 mx-1.5 px-2 py-1.5 text-sm rounded-md transition-colors ${
                  isActive
                    ? "bg-accent-bg text-accent font-medium"
                    : "text-text-secondary hover:bg-bg-hover hover:text-text-primary"
                }`}
              >
                {isActive
                  ? <FolderOpen size={13} style={{ color }} className="shrink-0" />
                  : <Folder size={13} style={{ color }} className="shrink-0" />}
                <span className="flex-1 font-mono text-xs truncate">/{vfsName}</span>
                {count > 0 && (
                  <span
                    className="text-xs px-1.5 py-0.5 rounded-full font-mono shrink-0 tabular-nums"
                    style={{ background: color + "18", color }}
                  >
                    {count > 9999 ? (count / 1000).toFixed(0) + "k" : count.toLocaleString()}
                  </span>
                )}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
