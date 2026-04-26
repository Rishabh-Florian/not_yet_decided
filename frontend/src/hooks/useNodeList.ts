import { useInfiniteQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import type { NodeListResponse } from "@/types/api";

export function useNodeList(type: string, limit = 50) {
  return useInfiniteQuery({
    queryKey: ["nodes", type],
    queryFn: ({ pageParam = 0 }) =>
      apiGet<NodeListResponse>(`/api/graph/nodes?type=${type}&limit=${limit}&offset=${pageParam}`),
    initialPageParam: 0,
    getNextPageParam: (last, _, lastPageParam) => {
      const fetched = (lastPageParam as number) + last.nodes.length;
      return fetched < last.total ? fetched : undefined;
    },
  });
}
