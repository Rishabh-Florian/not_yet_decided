# Pioneer.ai GLiNER2 fine-tune for `RouterTier`

This directory contains everything needed to fine-tune a GLiNER2 model
on Pioneer.ai for the `RouterTier` (Tier 2 of the retrieval cascade).
The runtime code that loads the resulting weights lives in
`backend/retrieval/router.py::GLiNER2EntityRouter`.

The cascade ships with a deterministic regex fallback
(`StubEntityRouter`) so the system is fully exercisable WITHOUT this
fine-tune. Replacing the stub with the fine-tuned GLiNER2 is a
production-quality upgrade: it adds NER recall on natural-language
queries that contain entities buried in prose
(`"send a message to Anil Rathore about ..."`).

## Files in this directory

| File                  | Purpose                                                                                                           |
|-----------------------|-------------------------------------------------------------------------------------------------------------------|
| `prompt.md`           | Pioneer.ai agent prompt — paste verbatim into the agent prompt slot when creating the fine-tuning job.            |
| `seed_examples.jsonl` | 50 hand-curated `{query, intent, entities}` examples spanning all 4 intents. Pioneer's synthetic data generator uses these to shape the larger training set so it matches our domain. |
| `eval_set.jsonl`      | 45 held-out items with the same shape, used post-fine-tune to measure intent accuracy and NER F1 vs the base GLiNER2. |
| `weights/`            | (created by you) the fine-tuned model directory you download from Pioneer. Pointed at by the env var `GLINER2_MODEL_PATH`. Gitignored. |

Re-derive `seed_examples.jsonl` and `eval_set.jsonl` after schema
changes:

```sh
uv run python scripts/gen_router_seed.py
```

## What the human needs to do (step by step)

1. **Sign up / log in to Pioneer.ai.**
   See https://wholesale-mackerel-22f.notion.site/Big-Berlin-Hack-Onboarding-3498413d474480319020ddb593d700c0
   for the hackathon onboarding link. You need an account before
   anything else works.

2. **Create a new fine-tuning job for GLiNER2 (multi-task).**
   In the Pioneer dashboard: New Job → Model = GLiNER2 → Task =
   "intent classification + NER (multi-task)".

3. **Paste the agent prompt.**
   Copy the entire body of `prompt.md` (this directory) into the
   "Agent prompt" / "System instructions" field. This locks the
   intent labels and entity schema — Pioneer's synthetic data
   generator follows the prompt.

4. **Upload `seed_examples.jsonl` as the seed/example data.**
   Path on this machine:
   `pioneer/seeds/seed_examples.jsonl`.
   Pioneer expects one JSON object per line:
   `{"query": "...", "intent": "...", "entities": {"<type>": ["<span>", ...]}}`.

5. **Configure synthetic-data generation.**
   Recommended starting hyperparameters (Pioneer defaults are usually
   fine — only change if the trained model under-performs):
   - Synthetic examples: 500–1000 (10–20× the seed count).
   - Class balance: enforce roughly equal counts per intent. The seed
     is intentionally lookup-heavy (matches EnterpriseBench task
     distribution); ask Pioneer to up-weight `analytical`,
     `ambiguous`, and `search` to roughly 25 % each in the synthetic
     set.
   - NER coverage: ensure every one of the 6 entity types
     (`emp_id`, `customer_id`, `ticket_id`, `date`, `department`,
     `product`) appears in at least 30 synthetic examples.

6. **Run the fine-tune.**
   Pioneer-managed; nothing for you to do except wait. Expected
   runtime: 15–45 minutes on Pioneer's infra.

7. **Evaluate against `eval_set.jsonl`.**
   Upload `pioneer/seeds/eval_set.jsonl` as the
   eval split. Targets:
   - **Intent accuracy ≥ 0.90** on the held-out 45 items.
   - **NER F1 ≥ 0.85** macro-averaged across the 6 entity types
     (rare types — `date`, `department` — may pull this down; track
     per-type F1).
   - Both numbers should beat base (un-fine-tuned) GLiNER2 by ≥ 10
     points. If they don't, increase synthetic count to 2000 and
     re-run; if still flat, the seed examples are not representative
     and need expansion.

8. **Download the fine-tuned weights.**
   Pioneer exposes them as a downloadable model directory (or HF Hub
   repo). Save under
   `pioneer/weights/<model-name>/` on the
   machine that will run the cascade. The directory should contain
   the standard GLiNER2 layout (`config.json`, `tokenizer.json`,
   `model.safetensors`, etc.).

9. **Wire the env vars.**
   Set in your shell / `.env`:
   ```sh
   QONTEXT_ROUTER=gliner2
   GLINER2_MODEL_PATH="C:/.../pioneer/weights/<model-name>"
   ```
   `build_orchestrator_with_store` reads `QONTEXT_ROUTER`. With it
   set to `gliner2`, the cascade boots `GLiNER2EntityRouter` instead
   of `StubEntityRouter`. Without `GLINER2_MODEL_PATH` set, the
   constructor raises (fail-fast).

10. **Install the inference dep.**
    The `gliner` package is intentionally NOT in `pyproject.toml`
    (~700 MB transitive: PyTorch, transformers, etc.). Install only
    on machines that will run the fine-tuned model:
    ```sh
    uv add gliner
    ```
    Without this, `GLiNER2EntityRouter.__init__` raises a clear
    `ImportError` pointing back to this README.

11. **Verify end-to-end.**
    ```sh
    QONTEXT_ROUTER=gliner2 GLINER2_MODEL_PATH=... \
      uv run pytest backend/retrieval/tests/test_router.py::TestGLiNER2EntityRouterIntegration -q
    ```
    These tests are skipped unless `GLINER2_MODEL_PATH` (or
    `PIONEER_AI_MODEL_ID`) is set in the environment.

12. **Re-run the eval harness with the fine-tuned router live.**
    ```sh
    QONTEXT_ROUTER=gliner2 GLINER2_MODEL_PATH=... \
      uv run python -m backend.eval.harness --limit 200
    ```
    Diff the output against the previous report under
    `backend/eval/reports/`. Acceptance: median latency drops vs the
    R3-only baseline; recall@10 unchanged or improved (per issue #6
    acceptance criteria).

## Output format from the model

The runtime parses Pioneer-fine-tuned GLiNER2 output via
`backend/retrieval/router.py::_parse_gliner2_output`. The expected
shape per query (one forward pass) is:

```json
{
  "classifications": {
    "intent": [
      {"label": "lookup", "score": 0.94},
      {"label": "search", "score": 0.04},
      {"label": "analytical", "score": 0.01},
      {"label": "ambiguous", "score": 0.01}
    ]
  },
  "entities": [
    {"label": "emp_id", "text": "emp_1002", "score": 0.99}
  ]
}
```

The parser keeps only the top intent and drops entities whose label
is not in the canonical 6-type schema. If your fine-tune emits a
slightly different shape, update `_parse_gliner2_output` (and add a
test in `test_router.py::TestParseGLiNER2Output`).

## Calibration

The default abstain threshold (`RouterTier.min_intent_conf=0.5`) is
a placeholder. After step 7 above, compute calibration on the eval
set: bin predicted intent scores into deciles and measure observed
accuracy per bin. If a bin is severely overconfident, raise
`min_intent_conf` accordingly. Track this number — it's the only
arbitrary float in the cascade, and per the project's "every
confidence is grounded" principle it must be backed by measurement,
not vibes.

## Pioneer side-challenge submission

Issue #6 is the **Pioneer.ai side-challenge entry (€700)** for best
use of a fine-tuned Pioneer model. The submission package is:

1. The fine-tuned model on Pioneer (link to the job page).
2. This README documenting the synthetic-data generation flow.
3. The eval delta: base GLiNER2 vs fine-tuned, on `eval_set.jsonl`.
4. The integration in `backend/retrieval/router.py` showing the
   model behind a Protocol with deterministic stub fallback (so the
   project still ships if the fine-tune is unavailable).

Append the eval numbers to the bottom of this file once they're in.

### Eval results

| Metric                    | Base GLiNER2 | Fine-tuned GLiNER2 | Delta |
|---------------------------|--------------|---------------------|-------|
| Intent accuracy           | TBD          | TBD                 | TBD   |
| NER F1 (macro)            | TBD          | TBD                 | TBD   |
| Tier latency p95 (CPU)    | TBD          | TBD                 | TBD   |
| Cascade median latency    | TBD          | TBD                 | TBD   |
| Cascade recall@10         | TBD          | TBD                 | TBD   |
