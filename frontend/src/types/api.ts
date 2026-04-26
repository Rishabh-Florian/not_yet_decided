export interface ProvenanceResponse {
  id: string;
  node_id?: string;
  edge_id?: string;
  source_file: string;
  source_record_id: string;
  source_field: string;
  extraction_method: "direct_mapping" | "llm_extraction" | "rule_based" | "human" | "synthetic" | string;
  extraction_model: string;
  confidence: number | "exact" | "grounded" | "inferred" | "human" | string;
  raw_value?: string;
  spec_version?: string;
  extracted_at: string;
}

export interface NodeResponse {
  id: string;
  type: string;
  attributes: Record<string, unknown>;
  provenance: ProvenanceResponse[];
  confidence: number;
  vfs_path?: string;
  created_at?: string;
  updated_at?: string;
  version: number;
}

export interface EdgeResponse {
  id: string;
  source_node_id: string;
  target_node_id: string;
  relation_type: string;
  attributes: Record<string, unknown>;
  provenance: ProvenanceResponse[];
  confidence: number;
  valid_from?: string;
  valid_to?: string;
  version: number;
}

export interface NodeListResponse {
  nodes: NodeResponse[];
  total: number;
  node_type: string;
}

export interface NeighborsResponse {
  node_id: string;
  neighbors: NodeResponse[];
}

export interface PathResponse {
  path: string[];
  length: number;
}

export interface PatternMatch {
  source: NodeResponse;
  edge: EdgeResponse;
  target: NodeResponse;
}

export interface PatternQueryResponse {
  pattern: string;
  matches: PatternMatch[];
  total: number;
}

export interface StatsResponse {
  graph: {
    node_count: number;
    edge_count: number;
    node_types: Record<string, number>;
    relation_types: Record<string, number>;
  };
  traces: {
    provenance_count: number;
  };
  raw: {
    source_record_count: number;
  };
}

export interface SourceRecordResponse {
  source_file: string;
  source_record_id: string;
  raw_record: Record<string, unknown>;
  content_hash: string;
  ingested_at: string;
}

export interface Candidate {
  value: unknown;
  confidence: string;
  source_file: string;
  spec_version: number | null;
}

export interface Conflict {
  id: number;
  node_id: string;
  attribute: string;
  existing: Candidate;
  incoming: Candidate;
  verdict: "LLM_TRIAGE" | "ESCALATE" | string;
  reason: string;
  status: "open" | "resolved";
  detected_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  chosen_value: unknown | null;
  resolution_method: "human" | "llm" | null;
}

export interface ConflictListResponse {
  conflicts: Conflict[];
  status: "open" | "resolved";
  total: number;
}

export interface OnboardResponse {
  spec_id: number | null;
  tenant: string;
  source_pattern: string;
  spec_version: number;
  status: "draft" | "active" | string;
  yaml_text: string;
  node_types: string[];
  edge_types: string[];
}
