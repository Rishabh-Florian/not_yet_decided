import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import type { SourceRecordResponse } from "@/types/api";

export function useSourceRecord(sourceFile: string | null, recordId: string | null) {
  return useQuery({
    queryKey: ["source-record", sourceFile, recordId],
    queryFn: () => apiGet<SourceRecordResponse>(`/api/source/${sourceFile}/${recordId}`),
    enabled: !!sourceFile && !!recordId,
  });
}
