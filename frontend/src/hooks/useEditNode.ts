import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiPut } from "@/lib/api-client";
import type { NodeResponse } from "@/types/api";

export function useEditNode(nodeId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ attributes, editor }: { attributes: Record<string, unknown>; editor: string }) =>
      apiPut<NodeResponse>(`/api/graph/node/${nodeId}`, { attributes, editor }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["node", nodeId] });
    },
  });
}
