import { create } from "zustand";

interface AppStore {
  selectedNodeId: string | null;
  setSelectedNodeId: (id: string | null) => void;
  provenanceFocusField: string | null;
  setProvenanceFocusField: (field: string | null) => void;
  activeQueryPattern: string;
  setActiveQueryPattern: (p: string) => void;
}

export const useAppStore = create<AppStore>((set) => ({
  selectedNodeId: null,
  setSelectedNodeId: (id) => set({ selectedNodeId: id }),
  provenanceFocusField: null,
  setProvenanceFocusField: (field) => set({ provenanceFocusField: field }),
  activeQueryPattern: "",
  setActiveQueryPattern: (p) => set({ activeQueryPattern: p }),
}));
