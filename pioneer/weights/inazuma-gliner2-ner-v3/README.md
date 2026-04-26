# inazuma-gliner2-ner-v3 — Pioneer LoRA adapter (NER-only)

LoRA adapter for `fastino/gliner2-base-v1`, trained as NER-only (no
intent classification head). ~11 MB.

## Files
- `adapter_config.json` — LoRA hyperparameters (r=16, alpha=32, target=encoder)
- `adapter_weights.safetensors` — delta weights
- `labels.json` — 6 entity types (no intent labels — see v2 for that)

## Eval (Round 3, 105-row held-out set)

| Entity | Base GLiNER2 | v2 schema | **v3 NER-only** |
|---|---|---|---|
| emp_id | 0.918 | 0.918 | **1.000** |
| customer_id | 0.667 | 0.222 | **0.941** |
| ticket_id | 0.750 | 0.000 | **0.909** |
| date | 0.840 | 0.857 | **0.920** |
| department | 0.667 | 0.581 | **0.863** |
| product | 0.273 | 0.000 | 0.471 |
| **MACRO** | 0.686 | 0.430 | **0.851** ✓ |
| p95 latency (Pioneer API) | 846 ms | 853 ms | 1536 ms |

Acceptance target macro NER F1 ≥ 0.85 → **met (0.851)**, beats base by **+16.5 pp**.

## Production wiring

Used for entity extraction only; intent classification stays on
`pioneer/weights/inazuma-gliner2-v2`. `TwoModelEntityRouter` calls
both in parallel via ThreadPoolExecutor.

```sh
# .env
QONTEXT_ROUTER=two-model
PIONEER_INTENT_MODEL_ID=683f9b1f-db87-4eba-9cf8-719b1350251d  # v2 schema
PIONEER_NER_MODEL_ID=ee1a87ae-2611-4eed-9f66-64437d40e0bb     # v3 NER
PIONEER_API_KEY=<your key>
```

Local fallback (gliner library + base model):

```python
from gliner import GLiNER
model = GLiNER.from_pretrained("fastino/gliner2-base-v1")
model.load_adapter("pioneer/weights/inazuma-gliner2-ner-v3")
entities = model.predict_entities(
    "Assign ticket 35181 to emp_0930 in the Engineering department",
    labels=["emp_id", "customer_id", "ticket_id", "date", "department", "product"],
    threshold=0.99,  # winning threshold from eval sweep
)
```

## Provenance

- Base: `fastino/gliner2-base-v1` (~205 M params)
- Pioneer job id: `ee1a87ae-2611-4eed-9f66-64437d40e0bb`
- Train: 497 templated queries from real graph entities (`pioneer/seeds/seed_examples_v2_ner.jsonl`)
- Eval: 105 held-out queries (`pioneer/seeds/eval_set_v2_ner.jsonl`)
- Hyperparams: 5 epochs, LR 5e-5, batch 4, threshold 0.99
