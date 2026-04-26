import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import type { NodeResponse } from "@/types/api";

export function useNodeDetail(id: string | null) {
  return useQuery({
    queryKey: ["node", id],
    queryFn: () => apiGet<NodeResponse>(`/api/graph/node/${id}`),
    enabled: !!id,
  });
}
