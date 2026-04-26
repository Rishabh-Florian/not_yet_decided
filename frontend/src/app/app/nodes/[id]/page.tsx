"use client";
import NodeDetailPanel from "@/components/nodes/NodeDetailPanel";

export default function NodeDetailPage({ params }: { params: { id: string } }) {
  return <NodeDetailPanel id={params.id} />;
}
