"use client";
import { useSourceRecord } from "@/hooks/useSourceRecord";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";

export default function SourceRecordDrawer({ sourceFile, recordId, onClose }: {
  sourceFile: string; recordId: string; onClose: () => void;
}) {
  const { data, isLoading } = useSourceRecord(sourceFile, recordId);

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-[480px] bg-bg-card border-border-color overflow-y-auto">
        <SheetHeader>
          <SheetTitle className="text-text-primary text-sm font-mono truncate">{sourceFile}</SheetTitle>
        </SheetHeader>
        <p className="text-xs font-mono text-text-tertiary mt-1 mb-4 truncate">{recordId}</p>
        {isLoading ? (
          <div className="space-y-2">
            {[...Array(6)].map((_, i) => <div key={i} className="h-4 bg-bg-hover rounded animate-pulse" />)}
          </div>
        ) : data ? (
          <pre className="text-xs font-mono text-text-mono bg-bg rounded-lg p-3 overflow-auto whitespace-pre-wrap break-all border border-border-color">
            {JSON.stringify(data.raw_record, null, 2)}
          </pre>
        ) : (
          <p className="text-xs text-text-tertiary">No data found.</p>
        )}
      </SheetContent>
    </Sheet>
  );
}
