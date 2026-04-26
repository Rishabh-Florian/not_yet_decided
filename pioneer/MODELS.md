# Pioneer fine-tuned models — what we shipped

Two fine-tuned LoRA adapters on `fastino/gliner2-base-v1` (~205 M
parameters). Each adapter is ~11 MB. Both downloaded at
`pioneer/weights/` (also mirrored on
[Google Drive](https://drive.google.com/drive/folders/1gH6r4uec2ElQlyXiIszvw8UxmpuQBSnD?usp=drive_link)).

Production wiring uses **both models in parallel** via
`TwoModelEntityRouter` — v2 handles intent, v3 handles NER, called from
two threads, results merged into a single `RouterDecision`. Same
end-to-end latency as a single model call.

---

## v2 — intent classifier (schema task)

| | |
|---|---|
| **Path** | `pioneer/weights/inazuma-gliner2-v2/` |
| **Pioneer job id** | `683f9b1f-db87-4eba-9cf8-719b1350251d` |
| **Task** | Multi-task schema (4-way intent + 6-type NER), but we use ONLY the intent head in production |
| **Why kept** | 0.978 intent accuracy on Round 2 eval — beats GPT-4o (0.867) by +11 pp at 2.6× lower latency |
| **Threshold** | 0.95 |
| **Trained on** | `inazuma-train-v2` (685 examples, mostly classification synth) |

NER head from this model is NOT used (macro F1 only 0.430 — diluted
by joint training). v3 below replaces it.

## v3 — NER-only

| | |
|---|---|
| **Path** | `pioneer/weights/inazuma-gliner2-ner-v3/` |
| **Pioneer job id** | `ee1a87ae-2611-4eed-9f66-64437d40e0bb` |
| **Task** | NER only (6 entity types: emp_id, customer_id, ticket_id, date, department, product) |
| **Why kept** | 0.851 macro NER F1 — meets acceptance target (≥0.85), beats v2 by **+42.1 pp** |
| **Threshold** | 0.99 |
| **Trained on** | `seed_examples_v2_ner.jsonl` (497 templated queries from real graph entities) |

Per-entity F1: emp_id 1.00, customer_id 0.94, ticket_id 0.91, date 0.92,
department 0.86, product 0.47.

---

## Why two models, not one

Tried Round 2 as a single multi-task (schema) fine-tune — intent
nailed at 0.978 but NER collapsed (ticket_id F1 = 0.000, product F1 =
0.000). The classification signal diluted the NER head.

Round 3 isolated NER as a single-task fine-tune on the same base
model. Macro NER F1 jumped from 0.430 → 0.851. Keeping v2 for intent
+ v3 for NER + parallel inference gives us the best of both.

Trade-off: 2× model storage (22 MB total — still trivial), 2× warm
memory at inference (covered by the threadpool fan-out).

---

## See also

- [`bench/results/comparison.md`](bench/results/comparison.md) — full 3-column eval tables (base / GPT-4o / v2 / v3) per round
- [`bench/results/screenshots/`](bench/results/screenshots/) — Pioneer UI captures (training jobs, eval views)
- [`seeds/`](seeds/) — current production training data (v2 templates) + archived v1 seeds
- [`prompt.md`](prompt.md) — the fine-tune system prompt (intent + entity schema)
