"use client";
import { Filter, RotateCcw, Search, Bookmark, Trash2, Plus, ChevronDown, ChevronUp } from "lucide-react";
import { useId, useState } from "react";
import { ALL_TYPES, useFilterStore, type ViewMode } from "@/store/filter-store";
import { NODE_TYPE_COLORS } from "@/lib/utils";

interface FilterPanelProps {
  visibleCount: number;
  totalCount: number;
  availableSources: string[];
  availableDepartments: string[];
  availableLocations: string[];
  maxDegree: number;
}

const TIME_OPTIONS: { label: string; days: number | null }[] = [
  { label: "All", days: null },
  { label: "24h", days: 1 },
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
];

const VIEW_MODES: { mode: ViewMode; label: string; title: string }[] = [
  { mode: "dim", label: "Dim", title: "Show all nodes; matched ones at full opacity, rest faded" },
  { mode: "isolate", label: "Isolate", title: "Show only matched nodes and edges between them" },
  { mode: "expand", label: "Expand", title: "Matched nodes plus all their direct neighbors" },
];

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pt-3 pb-1.5 text-[10px] font-medium uppercase tracking-widest text-text-tertiary">
      {children}
    </div>
  );
}

function FacetChip({
  label,
  active,
  anyActive,
  onToggle,
}: {
  label: string;
  active: boolean;
  anyActive: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      aria-pressed={active}
      title={active ? `Remove ${label} filter` : `Show only ${label}`}
      className={`rounded-md border px-2 py-0.5 font-mono text-[11px] transition-colors ${
        active
          ? "border-accent/40 bg-accent-bg text-accent"
          : anyActive
            ? "border-border-color-subtle bg-transparent text-text-tertiary hover:border-border-color hover:text-text-secondary"
            : "border-border-color-subtle bg-transparent text-text-secondary hover:border-border-color hover:text-text-primary"
      }`}
    >
      {label}
    </button>
  );
}

export default function FilterPanel({
  visibleCount,
  totalCount,
  availableSources,
  availableDepartments,
  availableLocations,
  maxDegree,
}: FilterPanelProps) {
  const entityTypes = useFilterStore((s) => s.entityTypes);
  const toggleEntityType = useFilterStore((s) => s.toggleEntityType);
  const timeWindowDays = useFilterStore((s) => s.timeWindowDays);
  const setTimeWindowDays = useFilterStore((s) => s.setTimeWindowDays);
  const minConnections = useFilterStore((s) => s.minConnections);
  const setMinConnections = useFilterStore((s) => s.setMinConnections);
  const sources = useFilterStore((s) => s.sources);
  const toggleSource = useFilterStore((s) => s.toggleSource);
  const searchQuery = useFilterStore((s) => s.searchQuery);
  const setSearchQuery = useFilterStore((s) => s.setSearchQuery);
  const subgraph = useFilterStore((s) => s.subgraph);
  const toggleDepartment = useFilterStore((s) => s.toggleDepartment);
  const toggleLocation = useFilterStore((s) => s.toggleLocation);
  const viewMode = useFilterStore((s) => s.viewMode);
  const setViewMode = useFilterStore((s) => s.setViewMode);
  const clearSubgraph = useFilterStore((s) => s.clearSubgraph);
  const savedViews = useFilterStore((s) => s.savedViews);
  const saveCurrentView = useFilterStore((s) => s.saveCurrentView);
  const loadView = useFilterStore((s) => s.loadView);
  const deleteView = useFilterStore((s) => s.deleteView);
  const reset = useFilterStore((s) => s.reset);

  const sliderId = useId();
  const sliderMax = Math.max(10, maxDegree);

  const anyDeptActive = (subgraph.departments?.size ?? 0) > 0;
  const anyLocActive = (subgraph.locations?.size ?? 0) > 0;
  const subgraphActive = anyDeptActive || anyLocActive;

  const [saveName, setSaveName] = useState("");
  const [showSavedViews, setShowSavedViews] = useState(false);

  function handleSave() {
    const name = saveName.trim();
    if (!name) return;
    saveCurrentView(name);
    setSaveName("");
  }

  return (
    <div
      className="flex min-h-0 flex-1 flex-col border-t border-border-color-subtle"
      aria-label="Graph filters"
    >
      <div className="flex items-center gap-2 border-b border-border-color-subtle px-3 py-2.5 text-xs font-medium uppercase tracking-widest text-text-tertiary">
        <Filter size={11} className="text-accent" />
        <span>Filters</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain pb-2">
        {/* Show node types */}
        <SectionLabel>Show</SectionLabel>
        <div className="space-y-0.5 px-1.5">
          {ALL_TYPES.map((type) => {
            const checked = entityTypes.has(type);
            const color = NODE_TYPE_COLORS[type] ?? "#7b7b7b";
            return (
              <label
                key={type}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs text-text-secondary transition-colors hover:bg-bg-hover"
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleEntityType(type)}
                  aria-label={`Show ${type} nodes`}
                  className="h-3 w-3 cursor-pointer accent-accent"
                />
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ background: color }}
                  aria-hidden
                />
                <span className="font-mono">{type}</span>
              </label>
            );
          })}
        </div>

        {/* Subgraph section */}
        {(availableDepartments.length > 0 || availableLocations.length > 0) && (
          <>
            <div className="mx-3 mt-3 mb-1.5 border-t border-border-color-subtle" />
            <div className="flex items-center justify-between px-3 pb-1.5">
              <span className="text-[10px] font-medium uppercase tracking-widest text-text-tertiary">
                Subgraph
              </span>
              {subgraphActive && (
                <button
                  onClick={clearSubgraph}
                  className="font-mono text-[10px] text-text-tertiary hover:text-accent transition-colors"
                  title="Clear subgraph filters"
                >
                  clear
                </button>
              )}
            </div>

            {/* View mode toggle — only shown when subgraph filter is active */}
            {subgraphActive && (
              <div className="px-3 pb-2">
                <div className="flex rounded-md border border-border-color-subtle overflow-hidden">
                  {VIEW_MODES.map(({ mode, label, title }) => (
                    <button
                      key={mode}
                      onClick={() => setViewMode(mode)}
                      title={title}
                      aria-pressed={viewMode === mode}
                      className={`flex-1 py-1 font-mono text-[10px] transition-colors ${
                        viewMode === mode
                          ? "bg-accent-bg text-accent"
                          : "text-text-tertiary hover:text-text-secondary"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Department chips */}
            {availableDepartments.length > 0 && (
              <>
                <div className="px-3 pb-0.5 text-[10px] font-medium text-text-tertiary">
                  Department
                </div>
                <div className="flex flex-wrap gap-1 px-3 pb-1.5">
                  {availableDepartments.map((dept) => (
                    <FacetChip
                      key={dept}
                      label={dept}
                      active={subgraph.departments?.has(dept) ?? false}
                      anyActive={anyDeptActive}
                      onToggle={() => toggleDepartment(dept)}
                    />
                  ))}
                </div>
              </>
            )}

            {/* Location chips */}
            {availableLocations.length > 0 && (
              <>
                <div className="px-3 pb-0.5 text-[10px] font-medium text-text-tertiary">
                  Location
                </div>
                <div className="flex flex-wrap gap-1 px-3 pb-1.5">
                  {availableLocations.map((loc) => (
                    <FacetChip
                      key={loc}
                      label={loc}
                      active={subgraph.locations?.has(loc) ?? false}
                      anyActive={anyLocActive}
                      onToggle={() => toggleLocation(loc)}
                    />
                  ))}
                </div>
              </>
            )}

            {/* Save current view */}
            {subgraphActive && (
              <div className="px-3 pb-1.5">
                <div className="flex gap-1">
                  <input
                    type="text"
                    value={saveName}
                    onChange={(e) => setSaveName(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleSave()}
                    placeholder="Name this view…"
                    className="flex-1 rounded-md border border-border-color-subtle bg-bg-hover px-2 py-1 font-mono text-[11px] text-text-primary placeholder:text-text-tertiary focus:border-accent/40 focus:outline-none"
                  />
                  <button
                    onClick={handleSave}
                    disabled={!saveName.trim()}
                    title="Save current view"
                    className="rounded-md border border-border-color-subtle px-2 py-1 text-text-tertiary transition-colors hover:border-accent/40 hover:text-accent disabled:opacity-30"
                  >
                    <Plus size={11} />
                  </button>
                </div>
              </div>
            )}

            {/* Saved views */}
            {savedViews.length > 0 && (
              <div className="px-3 pb-1.5">
                <button
                  onClick={() => setShowSavedViews((v) => !v)}
                  className="flex w-full items-center gap-1 text-[10px] font-medium text-text-tertiary hover:text-text-secondary transition-colors"
                >
                  <Bookmark size={9} />
                  <span>Saved views ({savedViews.length})</span>
                  {showSavedViews ? <ChevronUp size={9} className="ml-auto" /> : <ChevronDown size={9} className="ml-auto" />}
                </button>
                {showSavedViews && (
                  <div className="mt-1 space-y-0.5">
                    {savedViews.map((view) => (
                      <div key={view.id} className="flex items-center gap-1 rounded-md px-1 py-0.5 hover:bg-bg-hover group">
                        <button
                          onClick={() => loadView(view.id)}
                          className="flex-1 truncate text-left font-mono text-[11px] text-text-secondary hover:text-text-primary"
                          title={`Load: ${view.filter.departments?.join(", ") ?? "all"} / ${view.filter.locations?.join(", ") ?? "all"} (${view.viewMode})`}
                        >
                          {view.name}
                        </button>
                        <span className="font-mono text-[9px] text-text-tertiary">{view.viewMode}</span>
                        <button
                          onClick={() => deleteView(view.id)}
                          className="opacity-0 group-hover:opacity-100 text-text-tertiary hover:text-red-400 transition-all"
                          title="Delete saved view"
                        >
                          <Trash2 size={9} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="mx-3 mb-1 border-t border-border-color-subtle" />
          </>
        )}

        {/* Time */}
        <SectionLabel>Time</SectionLabel>
        <div
          role="radiogroup"
          aria-label="Time window"
          className="flex flex-wrap gap-1 px-3 pb-1"
        >
          {TIME_OPTIONS.map((opt) => {
            const active = timeWindowDays === opt.days;
            return (
              <button
                key={opt.label}
                role="radio"
                aria-checked={active}
                onClick={() => setTimeWindowDays(opt.days)}
                className={`rounded-md border px-2 py-0.5 font-mono text-[11px] transition-colors ${
                  active
                    ? "border-accent/40 bg-accent-bg text-accent"
                    : "border-border-color-subtle bg-transparent text-text-secondary hover:border-border-color hover:text-text-primary"
                }`}
              >
                {opt.label}
              </button>
            );
          })}
        </div>

        {/* Min connections */}
        <SectionLabel>Min connections</SectionLabel>
        <div className="px-3 pb-1">
          <div className="flex items-center justify-between pb-1 font-mono text-[11px] text-text-tertiary">
            <label htmlFor={sliderId} className="text-text-secondary">
              &ge; {minConnections}
            </label>
            <span>max {sliderMax}</span>
          </div>
          <input
            id={sliderId}
            type="range"
            min={0}
            max={sliderMax}
            value={Math.min(minConnections, sliderMax)}
            onChange={(e) => setMinConnections(Number(e.target.value))}
            aria-label="Minimum connections per node"
            aria-valuetext={`${minConnections} connections`}
            className="h-1 w-full cursor-pointer appearance-none rounded bg-border-color accent-accent"
          />
        </div>

        {/* Source */}
        <SectionLabel>Source</SectionLabel>
        <div className="space-y-0.5 px-1.5 pb-1">
          {availableSources.length === 0 ? (
            <div className="px-2 py-1 font-mono text-[11px] text-text-tertiary">
              none detected
            </div>
          ) : (
            availableSources.map((src) => {
              const active = sources == null || sources.size === 0 || sources.has(src);
              return (
                <label
                  key={src}
                  className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-xs text-text-secondary transition-colors hover:bg-bg-hover"
                >
                  <input
                    type="checkbox"
                    checked={active}
                    onChange={() => toggleSource(src)}
                    aria-label={`Show ${src} source`}
                    className="h-3 w-3 cursor-pointer accent-accent"
                  />
                  <span className="truncate font-mono">{src}</span>
                </label>
              );
            })
          )}
        </div>

        {/* Search */}
        <SectionLabel>Search</SectionLabel>
        <div className="px-3 pb-3">
          <div className="flex items-center gap-2 rounded-md border border-border-color-subtle bg-bg-hover px-2 py-1.5 focus-within:border-accent/40">
            <Search size={11} className="text-text-tertiary" />
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="name contains…"
              aria-label="Search nodes by name"
              className="w-full bg-transparent font-mono text-[11px] text-text-primary placeholder:text-text-tertiary focus:outline-none"
            />
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between gap-2 border-t border-border-color-subtle bg-bg/80 px-3 py-2 backdrop-blur-sm">
        <span className="font-mono text-[11px] text-text-secondary tabular-nums">
          <span className="text-text-primary">{visibleCount.toLocaleString()}</span>
          <span className="text-text-tertiary"> / {totalCount.toLocaleString()} visible</span>
        </span>
        <button
          onClick={reset}
          className="inline-flex items-center gap-1 rounded-md border border-border-color-subtle px-2 py-1 font-mono text-[11px] text-text-secondary transition-colors hover:border-accent/40 hover:text-accent"
        >
          <RotateCcw size={10} />
          Reset
        </button>
      </div>
    </div>
  );
}
