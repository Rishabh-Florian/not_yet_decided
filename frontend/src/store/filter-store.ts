import { create } from "zustand";
import { persist } from "zustand/middleware";
import { NODE_TYPES } from "@/lib/utils";

export const ALL_TYPES: string[] = [...NODE_TYPES];

// A subgraph view is defined by AND across dimensions, OR within each dimension.
// null means "no constraint on this dimension" (show all).
export interface SubgraphFilter {
  departments: Set<string> | null; // OR: show Persons in any of these depts
  locations: Set<string> | null;   // OR: show Persons in any of these locations
}

// How non-matching nodes are handled once a subgraph filter is active.
export type ViewMode = "dim" | "isolate" | "expand";
// dim      — all nodes visible; matching ones full opacity, rest faded
// isolate  — only matching nodes + edges strictly between them
// expand   — matching nodes + all their direct neighbors (cross-group edges visible)

export interface SavedView {
  id: string;
  name: string;
  filter: { departments: string[] | null; locations: string[] | null };
  viewMode: ViewMode;
}

interface FilterStore {
  // Standard graph filters
  entityTypes: Set<string>;
  timeWindowDays: number | null;
  minConnections: number;
  sources: Set<string> | null;
  searchQuery: string;

  // Subgraph filters
  subgraph: SubgraphFilter;
  viewMode: ViewMode;

  // Saved views (persisted)
  savedViews: SavedView[];

  // Actions
  toggleEntityType: (type: string) => void;
  setEntityTypesExclusive: (type: string) => void;
  setTimeWindowDays: (d: number | null) => void;
  setMinConnections: (n: number) => void;
  toggleSource: (s: string) => void;
  setSearchQuery: (q: string) => void;

  toggleDepartment: (d: string) => void;
  toggleLocation: (l: string) => void;
  setViewMode: (m: ViewMode) => void;
  clearSubgraph: () => void;

  saveCurrentView: (name: string) => void;
  loadView: (id: string) => void;
  deleteView: (id: string) => void;

  reset: () => void;
}

const defaultSubgraph: SubgraphFilter = {
  departments: null,
  locations: null,
};

const defaults = {
  entityTypes: new Set<string>(ALL_TYPES),
  timeWindowDays: null as number | null,
  minConnections: 0,
  sources: null as Set<string> | null,
  searchQuery: "",
  subgraph: defaultSubgraph,
  viewMode: "dim" as ViewMode,
  savedViews: [] as SavedView[],
};

function toggleInSet(current: Set<string> | null, value: string): Set<string> | null {
  const next = new Set(current ?? []);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next.size === 0 ? null : next;
}

export const useFilterStore = create<FilterStore>()(
  persist(
    (set, get) => ({
      ...defaults,

      toggleEntityType: (type) => {
        const next = new Set(get().entityTypes);
        if (next.has(type)) next.delete(type);
        else next.add(type);
        set({ entityTypes: next });
      },

      setEntityTypesExclusive: (type) => {
        const cur = get().entityTypes;
        const isAlreadyExclusive = cur.size === 1 && cur.has(type);
        set({ entityTypes: isAlreadyExclusive ? new Set(ALL_TYPES) : new Set([type]) });
      },

      setTimeWindowDays: (d) => set({ timeWindowDays: d }),
      setMinConnections: (n) => set({ minConnections: n }),

      toggleSource: (s) => {
        const cur = get().sources;
        const next = new Set(cur ?? []);
        if (next.has(s)) next.delete(s);
        else next.add(s);
        set({ sources: next.size === 0 ? null : next });
      },

      setSearchQuery: (q) => set({ searchQuery: q }),

      toggleDepartment: (d) =>
        set((s) => ({
          subgraph: { ...s.subgraph, departments: toggleInSet(s.subgraph.departments, d) },
        })),

      toggleLocation: (l) =>
        set((s) => ({
          subgraph: { ...s.subgraph, locations: toggleInSet(s.subgraph.locations, l) },
        })),

      setViewMode: (m) => set({ viewMode: m }),

      clearSubgraph: () =>
        set({ subgraph: defaultSubgraph, viewMode: "dim" }),

      saveCurrentView: (name) => {
        const { subgraph, viewMode, savedViews } = get();
        const view: SavedView = {
          id: Date.now().toString(36),
          name,
          filter: {
            departments: subgraph.departments ? Array.from(subgraph.departments) : null,
            locations: subgraph.locations ? Array.from(subgraph.locations) : null,
          },
          viewMode,
        };
        set({ savedViews: [...savedViews, view] });
      },

      loadView: (id) => {
        const view = get().savedViews.find((v) => v.id === id);
        if (!view) return;
        set({
          subgraph: {
            departments: view.filter.departments ? new Set(view.filter.departments) : null,
            locations: view.filter.locations ? new Set(view.filter.locations) : null,
          },
          viewMode: view.viewMode,
        });
      },

      deleteView: (id) =>
        set((s) => ({ savedViews: s.savedViews.filter((v) => v.id !== id) })),

      reset: () =>
        set({
          entityTypes: new Set<string>(ALL_TYPES),
          timeWindowDays: null,
          minConnections: 0,
          sources: null,
          searchQuery: "",
          subgraph: defaultSubgraph,
          viewMode: "dim",
        }),
    }),
    {
      name: "graph-filter-store",
      // Only persist savedViews — runtime filter state should start fresh
      partialize: (s) => ({ savedViews: s.savedViews }),
    }
  )
);
