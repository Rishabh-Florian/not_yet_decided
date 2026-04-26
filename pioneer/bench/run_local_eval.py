"""Run a fine-tuned (or base) GLiNER2 against `pioneer/seeds/eval_set.jsonl`
and emit JSON metrics to `pioneer/bench/results/<name>.json`.

Side-challenge measurement script. Pioneer's UI runs the same kind of
eval against base GLiNER2 + GPT-4o (Phase 0); this script is the local
counterpart for the fine-tuned weights once downloaded (Phase 3) so we
can compare like-for-like.

Usage:
    # base GLiNER2 (no fine-tune) — sanity check
    uv run python -m pioneer.bench.run_local_eval --name baseline-local

    # fine-tuned (set GLINER2_MODEL_PATH first)
    GLINER2_MODEL_PATH=pioneer/weights/gliner2-finetune-v1 \
        uv run python -m pioneer.bench.run_local_eval --name finetuned

Outputs metrics:
    intent_accuracy        — 4-way macro
    per_entity_f1          — dict, F1 per entity type
    macro_ner_f1           — mean of per_entity_f1 values
    p50_latency_ms         — median single-call latency
    p95_latency_ms         — 95th percentile
    n_eval                 — eval set size

Comparison against Pioneer's own baseline + GPT-4o eval lives in
`pioneer/bench/results/comparison.md` (filled in by hand).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

import backend.config  # noqa: F401  loads .env

from backend.retrieval.router import GLiNER2EntityRouter

ROOT = Path(__file__).resolve().parent.parent.parent
EVAL_PATH = ROOT / "pioneer" / "seeds" / "eval_set.jsonl"
RESULTS_DIR = ROOT / "pioneer" / "bench" / "results"


def load_eval() -> list[dict]:
    items = []
    with EVAL_PATH.open(encoding="utf-8") as f:
        for line in f:
            items.append(json.loads(line))
    return items


def evaluate(router: GLiNER2EntityRouter, items: list[dict]) -> dict:
    intent_correct = 0
    # Per-entity-type true positives, false positives, false negatives.
    tp: dict[str, int] = {}
    fp: dict[str, int] = {}
    fn: dict[str, int] = {}
    latencies_ms: list[float] = []

    for item in items:
        gold_intent = item["intent"]
        gold_entities: dict[str, list[str]] = item.get("entities", {}) or {}

        t0 = time.perf_counter()
        decision = router.classify(item["query"])
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

        if decision.intent == gold_intent:
            intent_correct += 1

        # Entity-level tp/fp/fn per type. Compare on lowercased verbatim
        # span sets — case-insensitive match, exact substring.
        all_types = set(gold_entities) | set(decision.entities)
        for t in all_types:
            gold = {s.lower() for s in gold_entities.get(t, [])}
            pred = {s.lower() for s in decision.entities.get(t, [])}
            tp[t] = tp.get(t, 0) + len(gold & pred)
            fp[t] = fp.get(t, 0) + len(pred - gold)
            fn[t] = fn.get(t, 0) + len(gold - pred)

    per_entity_f1 = {}
    for t in tp:
        precision = tp[t] / (tp[t] + fp[t]) if (tp[t] + fp[t]) else 0.0
        recall = tp[t] / (tp[t] + fn[t]) if (tp[t] + fn[t]) else 0.0
        per_entity_f1[t] = (
            2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        )

    macro_ner_f1 = statistics.mean(per_entity_f1.values()) if per_entity_f1 else 0.0
    return {
        "intent_accuracy": intent_correct / len(items),
        "per_entity_f1": per_entity_f1,
        "macro_ner_f1": macro_ner_f1,
        "p50_latency_ms": statistics.median(latencies_ms),
        "p95_latency_ms": statistics.quantiles(latencies_ms, n=20)[18],
        "n_eval": len(items),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Result file basename (e.g. 'finetuned').")
    parser.add_argument(
        "--model-path",
        default=os.environ.get("GLINER2_MODEL_PATH"),
        help="Override GLINER2_MODEL_PATH for this run.",
    )
    args = parser.parse_args()

    if not args.model_path:
        raise SystemExit(
            "GLINER2_MODEL_PATH not set. Either export it or pass --model-path. "
            "For base GLiNER2: --model-path knowledgator/gliner2-base"
        )

    os.environ["GLINER2_MODEL_PATH"] = args.model_path
    router = GLiNER2EntityRouter()
    items = load_eval()
    metrics = evaluate(router, items)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{args.name}.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "model_path": args.model_path,
                "eval_set": str(EVAL_PATH.relative_to(ROOT)),
                "metrics": metrics,
            },
            f,
            indent=2,
        )
    print(f"wrote {out}")
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
