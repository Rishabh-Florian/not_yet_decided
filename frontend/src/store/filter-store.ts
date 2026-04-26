import { create } from "zustand";
import { NODE_TYPES } from "@/lib/utils";

export const ALL_TYPES: string[] = [...NODE_TYPES];

interface FilterStore {
  entityTypes: Set<string>;
  timeWindowDays: number | null;
  minConnections: number;
  sources: Set<string> | null;
  searchQuery: string;

  toggleEntityType: (type: string) => void;
  setEntityTypesExclusive: (type: string) => void;
  setTimeWindowDays: (d: number | null) => void;
  setMinConnections: (n: number) => void;
  toggleSource: (s: string) => void;
  setSearchQuery: (q: string) => void;
  reset: () => void;
}

const defaults = {
  entityTypes: new Set<string>(ALL_TYPES),
  timeWindowDays: null as number | null,
  minConnections: 0,
  sources: null as Set<string> | null,
  searchQuery: "",
};

export const useFilterStore = create<FilterStore>((set, get) => ({
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
    set({ sources: next });
  },

  setSearchQuery: (q) => set({ searchQuery: q }),

  reset: () =>
    set({
      entityTypes: new Set<string>(ALL_TYPES),
      timeWindowDays: null,
      minConnections: 0,
      sources: null,
      searchQuery: "",
    }),
}));
