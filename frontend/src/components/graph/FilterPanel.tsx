"use client";
import { Filter, RotateCcw, Search } from "lucide-react";
import { useId } from "react";
import { ALL_TYPES, useFilterStore } from "@/store/filter-store";
import { NODE_TYPE_COLORS } from "@/lib/utils";

interface FilterPanelProps {
  visibleCount: number;
  totalCount: number;
  availableSources: string[];
  maxDegree: number;
}

const TIME_OPTIONS: { label: string; days: number | null }[] = [
  { label: "All", days: null },
  { label: "24h", days: 1 },
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
];

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pt-3 pb-1.5 text-[10px] font-medium uppercase tracking-widest text-text-tertiary">
      {children}
    </div>
  );
}

export default function FilterPanel({
  visibleCount,
  totalCount,
  availableSources,
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
  const reset = useFilterStore((s) => s.reset);

  const sliderId = useId();
  const sliderMax = Math.max(10, maxDegree);

  return (
    <div
      className="flex min-h-0 flex-1 flex-col border-t border-border-color-subtle"
      aria-label="Graph filters"
    >
      <div className="flex items-center gap-2 border-b border-border-color-subtle px-3 py-2.5 text-xs font-medium uppercase tracking-widest text-text-tertiary">
        <Filter size={11} className="text-accent" />
        <span>Filters</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {/* Show */}
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
              ≥ {minConnections}
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
