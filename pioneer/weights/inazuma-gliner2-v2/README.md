# inazuma-gliner2-v2 — Pioneer LoRA adapter

LoRA adapter for `fastino/gliner2-base-v1`. ~11 MB.

## Files
- `adapter_config.json` — LoRA hyperparameters (r=16, alpha=32, target=encoder)
- `adapter_weights.safetensors` — delta weights
- `labels.json` — schema labels (4 intents + 6 entity types)

## Eval (Round 2, 45-row held-out set)

| Metric | Base | GPT-4o | v2 |
|---|---|---|---|
| Intent accuracy | 0.533 | 0.867 | **0.978** |
| Macro NER F1 | 0.300 | 0.337 | 0.364 |
| p95 (Pioneer API) | 991 ms | 1699 ms | 2606 ms |
| p95 (local CPU expected) | — | — | 50–150 ms |

## Loading

Base + adapter (`gliner` library):
```python
from gliner import GLiNER
model = GLiNER.from_pretrained("fastino/gliner2-base-v1")
model.load_adapter("pioneer/weights/inazuma-gliner2-v2")
```

Or hit the Pioneer hosted endpoint with model id
`683f9b1f-db87-4eba-9cf8-719b1350251d`.

Wire into cascade: `BETTER_CONTEXT_ROUTER=gliner2 GLINER2_MODEL_PATH=pioneer/weights/inazuma-gliner2-v2`.
Without those, cascade falls back to `StubEntityRouter` (regex).

See `pioneer/README.md` and `pioneer/bench/results/comparison.md`.
