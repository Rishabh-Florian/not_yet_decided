import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import type { StatsResponse } from "@/types/api";

export function useGraphStats() {
  return useQuery({
    queryKey: ["graph-stats"],
    queryFn: () => apiGet<StatsResponse>("/api/graph/stats"),
    staleTime: 60_000,
  });
}
