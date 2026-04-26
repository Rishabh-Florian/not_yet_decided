"""Convert our seed/eval JSONL to the schema Pioneer's NER task accepts.

Our format (per row):
    {"query": "...", "intent": "...", "entities": {"emp_id": ["x"], ...}}

Pioneer NER task format (per row):
    {"text": "...", "entities": [["x", "emp_id"], ...]}

Pioneer's NER task discards intent (use the Classification task for that
in a separate upload). Keeps `intent` as a comment field for our records,
ignored by Pioneer's parser.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def convert(in_path: Path, out_path: Path) -> int:
    n = 0
    with in_path.open(encoding="utf-8") as fin, out_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            text = row["query"]
            ents_dict = row.get("entities", {}) or {}
            ents_list: list[list[str]] = []
            for label, spans in ents_dict.items():
                if not isinstance(spans, list):
                    continue
                for span in spans:
                    if isinstance(span, str) and span:
                        ents_list.append([span, label])
            out = {"text": text, "entities": ents_list}
            fout.write(json.dumps(out, ensure_ascii=False) + "\n")
            n += 1
    return n


def main() -> None:
    pairs = [
        (ROOT / "seed_examples_v2.jsonl", ROOT / "seed_examples_v2_ner.jsonl"),
        (ROOT / "eval_set_v2.jsonl", ROOT / "eval_set_v2_ner.jsonl"),
    ]
    for src, dst in pairs:
        if not src.exists():
            print(f"skip {src.name} — not found")
            continue
        n = convert(src, dst)
        print(f"wrote {n} rows -> {dst.name}")


if __name__ == "__main__":
    main()
