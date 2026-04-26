"use client";
import { useSearchParams } from "next/navigation";
import NodeListTable from "@/components/nodes/NodeListTable";
import { Suspense } from "react";

function NodeListContent() {
  const searchParams = useSearchParams();
  const type = searchParams.get("type") ?? "Person";
  return <NodeListTable type={type} />;
}

export default function NodesPage() {
  return (
    <Suspense>
      <NodeListContent />
    </Suspense>
  );
}
