import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export const NODE_TYPE_COLORS: Record<string, string> = {
  Person:       "#f2f2f2",
  Organization: "#dfdfdf",
  Document:     "#cccccc",
  Message:      "#b7b7b7",
  Event:        "#a9a9a9",
  Asset:        "#989898",
  Topic:        "#878787",
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
  return NODE_TYPE_COLORS[type] ?? "#7b7b7b";
}

export function formatConfidence(c: number): string {
  return (c * 100).toFixed(0) + "%";
}

export function confidenceColor(c: number): string {
  if (c >= 0.85) return "#f1f1f1";
  if (c >= 0.60) return "#bdbdbd";
  return "#7d7d7d";
}

export function nodeDisplayName(node: { id: string; attributes: Record<string, unknown> }): string {
  const a = node.attributes;
  return String(a.name ?? a.subject ?? a.title ?? a.email ?? node.id);
}
