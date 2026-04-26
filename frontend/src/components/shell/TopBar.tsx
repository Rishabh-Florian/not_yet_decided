"use client";
import { useGraphStats } from "@/hooks/useGraphStats";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { GitGraph } from "lucide-react";

const NAV = [
  { label: "Graph",     href: "/app/graph" },
  { label: "Nodes",     href: "/app/nodes?type=Person" },
  { label: "Query",     href: "/app/query" },
  { label: "Conflicts", href: "/app/conflicts" },
  { label: "Onboard",   href: "/app/onboard" },
];

export default function TopBar() {
  const { data: stats } = useGraphStats();
  const pathname = usePathname();

  return (
    <header className="h-14 flex items-center gap-4 px-5 border-b border-border-color bg-bg-card shrink-0">
      <Link href="/" className="flex items-center gap-2.5 hover:opacity-80 transition-opacity">
        <GitGraph size={20} className="text-accent" />
        <span className="text-base font-semibold text-text-primary tracking-tight">Better Context</span>
      </Link>

      <div className="w-px h-4 bg-border-color" />

      <span className="text-xs text-text-tertiary font-mono">
        {stats
          ? `${stats.graph.node_count.toLocaleString()} nodes · ${stats.graph.edge_count.toLocaleString()} edges`
          : "connecting…"}
      </span>

      <nav className="flex items-center gap-0.5 ml-auto">
        {NAV.map(({ label, href }) => {
          const base = href.split("?")[0];
          const active = pathname.startsWith(base);
          return (
            <Link
              key={label}
              href={href}
              className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                active
                  ? "bg-accent-bg text-accent font-medium"
                  : "text-text-secondary hover:bg-bg-hover hover:text-text-primary"
              }`}
            >
              {label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
