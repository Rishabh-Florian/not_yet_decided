const STRING_CONFIDENCE: Record<string, { label: string; color: string }> = {
  exact:    { label: "exact",    color: "#e6e6e6" },
  grounded: { label: "grounded", color: "#c2c2c2" },
  inferred: { label: "inferred", color: "#aaaaaa" },
  human:    { label: "human",    color: "#969696" },
};

export default function ConfidencePill({ confidence }: { confidence: number | string }) {
  if (typeof confidence === "string") {
    const meta = STRING_CONFIDENCE[confidence] ?? { label: confidence, color: "#727272" };
    return (
      <span
        className="text-xs px-1.5 py-0.5 rounded-full font-mono font-medium"
        style={{ background: meta.color + "18", color: meta.color }}
      >
        {meta.label}
      </span>
    );
  }

  const pct = Math.round(confidence * 100);
  const color = confidence >= 0.9 ? "#e6e6e6" : confidence >= 0.7 ? "#aaaaaa" : "#727272";
  return (
    <span
      className="text-xs px-1.5 py-0.5 rounded-full font-mono font-medium"
      style={{ background: color + "18", color }}
    >
      {pct}%
    </span>
  );
}
