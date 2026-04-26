import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import type { PathResponse } from "@/types/api";

export function usePath(fromId: string | null, toId: string | null, maxHops = 6) {
  return useQuery({
    queryKey: ["path", fromId, toId, maxHops],
    queryFn: () => apiGet<PathResponse>(`/api/graph/path?from=${fromId}&to=${toId}&max_hops=${maxHops}`),
    enabled: !!fromId && !!toId,
  });
}
