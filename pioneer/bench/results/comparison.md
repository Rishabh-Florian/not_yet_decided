# Pioneer GLiNER2 fine-tune — eval comparison

> Fill in once Phase 0 (Pioneer baseline + GPT-4o) and Phase 3
> (fine-tuned local) results land. The headline numbers below feed the
> side-challenge submission.

## Eval set

`pioneer/seeds/eval_set.jsonl` — 45 held-out queries (30 lookup / 7
search / 4 analytical / 4 ambiguous). Schema: 4-way intent +
6 entity types.

## Three-column comparison

| Metric | Base GLiNER2 (Pioneer eval) | GPT-4o (Pioneer eval) | Fine-tuned GLiNER2 (local) | Δ vs base | Δ vs GPT-4o |
|---|---|---|---|---|---|
| Intent accuracy | TBD | TBD | TBD | TBD | TBD |
| Macro NER F1 | TBD | TBD | TBD | TBD | TBD |
| Per-entity F1 — `emp_id` | TBD | TBD | TBD | TBD | TBD |
| Per-entity F1 — `customer_id` | TBD | TBD | TBD | TBD | TBD |
| Per-entity F1 — `ticket_id` | TBD | TBD | TBD | TBD | TBD |
| Per-entity F1 — `date` | TBD | TBD | TBD | TBD | TBD |
| Per-entity F1 — `department` | TBD | TBD | TBD | TBD | TBD |
| Per-entity F1 — `product` | TBD | TBD | TBD | TBD | TBD |
| p95 latency (ms) | TBD | TBD | TBD | TBD | TBD |
| Cost per 1k queries (USD) | $0 | ~$5 | $0 | flat | infinite |

## How to fill this

1. **Pioneer side** — export the Pioneer eval reports for base GLiNER2 + GPT-4o into `baseline.json` and `gpt4o.json` in this folder. Copy the headline numbers into the table above.
2. **Local side** — once you download the fine-tuned weights, run:
   ```sh
   GLINER2_MODEL_PATH=pioneer/weights/<model-name> \
     uv run python -m pioneer.bench.run_local_eval --name finetuned
   ```
   Reads `finetuned.json` from `pioneer/bench/results/`. Copy headline numbers into the table.

## Submission package (€700 side challenge)

1. Pioneer fine-tune job link (paste here)
2. This `comparison.md` (filled in)
3. `pioneer/README.md` — workflow + prompt + seed schema
4. Integration code: `backend/retrieval/router.py:GLiNER2EntityRouter` (already shipped, env-gated)
5. Live demo: `QONTEXT_ROUTER=gliner2 uv run uvicorn backend.api.app:app` then `POST /api/query` with a freeform NL question that the fine-tuned router should classify as `analytical` and route to AgenticTier
