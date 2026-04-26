"use client";
import EditForm from "@/components/edit/EditForm";

export default function EditPage({ params }: { params: { id: string } }) {
  return <EditForm nodeId={params.id} />;
}
