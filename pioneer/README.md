# Pioneer fine-tunes for the retrieval cascade

This directory holds everything related to our Pioneer.ai fine-tuned
GLiNER2 models that power the `RouterTier` (Tier 2 of the retrieval
cascade). Two production models shipped here; details + numbers in
[`MODELS.md`](MODELS.md).

## TL;DR

| Model | Purpose | Headline |
|---|---|---|
| `weights/inazuma-gliner2-v2/` | Intent classifier (4 labels) | **0.978** intent acc — beats GPT-4o by +11 pp |
| `weights/inazuma-gliner2-ner-v3/` | NER extractor (6 entity types) | **0.851** macro NER F1 — meets the ≥0.85 target |

Both are LoRA adapters (~11 MB each) on `fastino/gliner2-base-v1`. Used
together via `TwoModelEntityRouter` (parallel inference), they replace
the regex-based `StubEntityRouter` in production. Without either set,
the cascade still boots — the stub keeps everything green.

## Layout

```
pioneer/
├── MODELS.md                      # what we shipped, with eval numbers
├── README.md                      # (this file) — index
├── prompt.md                      # the fine-tune system prompt (intent + entity schema)
├── seeds/
│   ├── gen_dataset_v2.py          # template-based generator (production)
│   ├── seed_examples_v2.jsonl     # 497 queries — intent + entity labels (joint format)
│   ├── seed_examples_v2_ner.jsonl # same 497, Pioneer NER-task format ([span,label] pairs)
│   ├── eval_set_v2.jsonl          # 105 held-out — intent + entity labels (joint)
│   ├── eval_set_v2_ner.jsonl      # same 105, Pioneer NER-task format
│   ├── convert_to_pioneer_ner.py  # joint-format → NER-task-format converter
│   └── _v1_archive/               # superseded regex-derived seeds (Round 1+2)
├── bench/
│   ├── run_local_eval.py          # validate downloaded weights against eval_set
│   └── results/
│       ├── comparison.md          # 3-column eval tables per round
│       └── screenshots/           # Pioneer UI captures (training jobs, eval views)
└── weights/
    ├── inazuma-gliner2-v2/        # intent classifier (Round 2, schema task)
    └── inazuma-gliner2-ner-v3/    # NER-only (Round 3)
```

## Pipeline summary

```
            ┌──────────────────────────────────────┐
            │  pioneer/seeds/gen_dataset_v2.py     │
            │  (templates × real graph entities)   │
            └──────────────────┬───────────────────┘
                               │
                seeds_v2.jsonl │ eval_v2.jsonl
                               ▼
            ┌──────────────────────────────────────┐
            │  Pioneer agent: NER-task fine-tune   │
            │  (5 epochs, LR 5e-5, batch 4)        │
            └──────────────────┬───────────────────┘
                               │
                  LoRA adapter │ ~11 MB
                               ▼
            ┌──────────────────────────────────────┐
            │  pioneer/weights/inazuma-gliner2-*   │
            │  (committed in-repo + GDrive mirror) │
            └──────────────────┬───────────────────┘
                               │
                               ▼
            ┌──────────────────────────────────────┐
            │  TwoModelEntityRouter                │
            │  (intent + NER in parallel threads)  │
            └──────────────────────────────────────┘
```

## Re-train workflow (if you ever change the schema)

1. Edit entity types or intent labels in `prompt.md` and
   `backend/retrieval/router.py::ENTITY_TYPES` / `INTENTS`.
2. Re-derive seeds: `uv run python pioneer/seeds/gen_dataset_v2.py`
   (writes the joint-format files).
3. Re-derive Pioneer NER-task format:
   `uv run python pioneer/seeds/convert_to_pioneer_ner.py`.
4. Upload `seed_examples_v2_ner.jsonl` and `eval_set_v2_ner.jsonl`
   under Pioneer's NER tab (intent training is a separate step on
   the Classification tab if you want to retrain v2 too).
5. Paste `prompt.md` as the agent prompt.
6. Train. Download weights to `pioneer/weights/<new-name>/`.
7. Update `pioneer/bench/results/comparison.md` with the new numbers.
8. If beating production: update env vars (`PIONEER_NER_MODEL_ID`
   etc.) to point at the new model.

## Results

See [`bench/results/comparison.md`](bench/results/comparison.md) for
the full eval tables (base GLiNER2 / GPT-4o / v2 schema / v3 NER-only)
and [`bench/results/screenshots/`](bench/results/screenshots/) for
Pioneer UI captures.

## Side-challenge submission package (€700)

1. Pioneer fine-tune jobs:
   - v2 schema: `683f9b1f-db87-4eba-9cf8-719b1350251d`
   - v3 NER-only: `ee1a87ae-2611-4eed-9f66-64437d40e0bb`
2. [`bench/results/comparison.md`](bench/results/comparison.md) — 3-col tables (base / GPT-4o / fine-tuned) for both rounds
3. [`MODELS.md`](MODELS.md) — what we shipped, why two models
4. Integration code: `backend/retrieval/router.py:GLiNER2EntityRouter` (already in main; `TwoModelEntityRouter` follow-up)
5. Live demo: frontend `/api/query` chatbar → cascade → fine-tuned router with intent + entities
