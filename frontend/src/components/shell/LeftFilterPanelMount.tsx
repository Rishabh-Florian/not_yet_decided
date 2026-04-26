"use client";
import { usePathname } from "next/navigation";
import FilterPanel from "@/components/graph/FilterPanel";
import { useGraphData } from "@/components/graph/graph-data";
import { useFilteredGraph } from "@/components/graph/useFilteredGraph";

export default function LeftFilterPanelMount() {
  const pathname = usePathname();
  const onGraph = pathname?.startsWith("/app/graph");

  const { data } = useGraphData();
  const filtered = useFilteredGraph(data?.nodes ?? [], data?.links ?? []);

  if (!onGraph) return null;

  return (
    <FilterPanel
      visibleCount={filtered.visibleCount}
      totalCount={filtered.totalCount}
      availableSources={filtered.availableSources}
      maxDegree={filtered.maxDegree}
    />
  );
}
