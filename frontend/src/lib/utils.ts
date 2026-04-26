import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export const NODE_TYPE_COLORS: Record<string, string> = {
  Person:       "#3b82f6",
  Organization: "#8b5cf6",
  Document:     "#f59e0b",
  Message:      "#06b6d4",
  Event:        "#ec4899",
  Asset:        "#10b981",
  Topic:        "#f97316",
};

export const NODE_TYPES = ["Person", "Organization", "Document", "Message", "Event", "Asset", "Topic"] as const;
export const RELATION_TYPES = [
  "MEMBER_OF", "REPORTS_TO", "WORKS_ON", "OWNS", "AUTHORED",
  "SENT", "RECEIVED", "MENTIONS", "PART_OF", "PURCHASED",
  "ASSIGNED_TO", "TAGGED", "RELATED_TO", "SAME_AS",
] as const;

export const NODE_TYPE_VFS: Record<string, string> = {
  Person:       "people",
  Organization: "orgs",
  Document:     "documents",
  Message:      "messages",
  Event:        "events",
  Asset:        "assets",
  Topic:        "topics",
};

export function nodeTypeColor(type: string): string {
  return NODE_TYPE_COLORS[type] ?? "#64748b";
}

export function formatConfidence(c: number): string {
  return (c * 100).toFixed(0) + "%";
}

export function confidenceColor(c: number): string {
  if (c >= 0.85) return "#16a34a";
  if (c >= 0.60) return "#d97706";
  return "#dc2626";
}

export function nodeDisplayName(node: { id: string; attributes: Record<string, unknown> }): string {
  const a = node.attributes;
  return String(a.name ?? a.subject ?? a.title ?? a.email ?? node.id);
}

