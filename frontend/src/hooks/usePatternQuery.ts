import { useMutation } from "@tanstack/react-query";
import { apiPost } from "@/lib/api-client";
import type { PatternQueryResponse } from "@/types/api";

export function usePatternQuery() {
  return useMutation({
    mutationFn: ({ pattern, limit = 50, offset = 0 }: { pattern: string; limit?: number; offset?: number }) =>
      apiPost<PatternQueryResponse>("/api/graph/query", { pattern, limit, offset }),
  });
}
