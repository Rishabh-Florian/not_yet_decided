import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import type { NeighborsResponse } from "@/types/api";

export function useNeighbors(nodeId: string | null, depth = 1, relationType?: string) {
  const params = new URLSearchParams({ depth: String(depth) });
  if (relationType) params.set("relation_type", relationType);
  return useQuery({
    queryKey: ["neighbors", nodeId, depth, relationType],
    queryFn: () => apiGet<NeighborsResponse>(`/api/graph/node/${nodeId}/neighbors?${params}`),
    enabled: !!nodeId,
  });
}
