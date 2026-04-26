import { confidenceColor, formatConfidence } from "@/lib/utils";

export default function ConfidencePill({ confidence }: { confidence: number }) {
  const color = confidenceColor(confidence);
  return (
    <span
      className="text-xs px-1.5 py-0.5 rounded-full font-mono font-medium"
      style={{ background: color + "18", color }}
    >
      {formatConfidence(confidence)}
    </span>
  );
}
