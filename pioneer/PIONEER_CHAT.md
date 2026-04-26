# Pioneer chat — copy-paste sequence

What to send to the Pioneer agent, in order.

> **Yes, upload the two .jsonl files** as separate messages (drag-and-drop
> into Pioneer's chat). Only the system prompt needs pasting as text.

## File paths on this machine

```
pioneer/seeds/seed_examples.jsonl   (170 lines, ~50 KB) — drag-drop into chat
pioneer/seeds/eval_set.jsonl        (45 lines,  ~9 KB)  — drag-drop into chat
```

---

## ROUND 2 (after the customer_id fix)

If you already pasted in Round 1 and Pioneer flagged `customer_id: 0`, re-upload the new `seed_examples.jsonl` (it now has 5 customer_id labels — it was 0 before). Send this message with the new upload:

```
Re-uploading seed_examples.jsonl — fixed customer_id labelling (was 0
mentions, now 5). The EnterpriseBench source data references customer
shortnames like `customer_id koene`, `Customer ID bolid`, `customer_id:
ernsh`. Original regex only matched `CLNT-NNNN` style prefixed ids which
don't appear in this dataset. Updated regex catches the inline shortname
form.

Per-type seed counts now:
  emp_id: 169  customer_id: 5  ticket_id: 2  date: 11  department: 37  product: 17

ticket_id and date are still sparse (2 and 11) because EnterpriseBench
tasks rarely include them — please oversample BOTH heavily during synth
expansion (target >=80 each per the original ask) so per-type F1 is
measurable at eval time.

Also: the eval_set.jsonl I uploaded earlier — please load it from the
uploaded file path (the same way you loaded the seed), not by inline-
string-embedding it in a Python heredoc. Apostrophes in customer names
broke the bash quoting in your earlier validation script. Re-attaching
the eval file just in case.
```

Then drag-drop both `seed_examples.jsonl` AND `eval_set.jsonl` again.

---

## ROUND 1 (first-time messages — skip if already sent)

### Message 1 — opener + system prompt + setup notes

````
Hi! Plan: single GLiNER2 multi-task model (one classification head + one
NER head, single forward pass — schema composition per your docs). NOT two
separate models. 4 intent labels, 6 entity types.

Files I will upload next: seed_examples.jsonl (170) + eval_set.jsonl (45).

PLEASE before kicking off the fine-tune: run a baseline eval (base GLiNER2
+ Pioneer-hosted GPT-4o) against the eval set and send back the 3-column
table (intent acc + per-entity F1 + macro F1 + p95 latency). I want to
confirm the baseline isn't already saturated and our schema is sane before
spending fine-tune cycles.

SYNTHETIC EXPANSION: my seed is ~88% lookup (artifact of the source
dataset being command-style task instructions). For the synth pass please
rebalance to roughly:
  lookup: 40%, search: 25%, analytical: 25%, ambiguous: 10%
Each of the 6 entity types should appear in >=80 synthetic examples so
per-type F1 is statistically meaningful at eval time. The seed
under-represents `customer_id`, `ticket_id`, `date` — please oversample
those.

ACCEPTANCE TARGETS:
- Intent accuracy >= 0.90
- Macro NER F1 >= 0.85
- Both beating base GLiNER2 by 10+ points

GPT-4o for the baseline column: please use the Pioneer-hosted endpoint —
no OpenAI key on my side.

------ SYSTEM PROMPT (use as the agent prompt / system instructions) ------

You are an entity-extraction and intent-classification model for an
enterprise knowledge-graph retrieval system called Better Context. Your
job, in one forward pass, is to:

1. Classify the user's query into exactly one of four intents.
2. Extract every named entity that appears in the query, labelled by type
   from a fixed schema.

The system uses your output to route the query to the right downstream
retrieval tier. Wrong routing wastes latency; missed entities reduce
recall. You are NOT a chat assistant — never respond with prose.

INTENT LABELS (pick exactly one):
- lookup: the user names a specific entity by id or unambiguous handle
  (emp_1002, CLNT-0042, ticket-4226, an ASIN, a UUID).
- search: natural-language question or instruction with no exact
  identifier — needs semantic / lexical retrieval.
- analytical: requires aggregation, counting, comparison, ranking, or
  multi-hop reasoning. Keywords: how many, count, average, compare, trend.
- ambiguous: too short, too generic, or no actionable signal (help,
  status, data, single non-entity tokens).

If two labels seem to apply, pick the one with the most specific
downstream action (lookup > analytical > search > ambiguous).

ENTITY SCHEMA (six types, verbatim spans, multiple per type allowed, drop
everything else):
- emp_id: employee identifier of shape emp_NNNN.
  Examples: emp_1002, emp_0431
- customer_id: customer / client / vendor identifier (CLNT-, CUST-, VEND-,
  ORG- prefix; or short customer shortname like arout, bolid, koene,
  ernsh, blonp, linod, victe, godos, commi, merep — typically appears
  after the literal token "customer_id" or "Customer ID:").
  Examples: CLNT-0042, CUST-0007, bolid, koene
- ticket_id: IT support ticket identifier (raw integer or ticket-NNNN).
  Examples: ticket-4226, Ticket id 9117
- date: a date or date range — ISO 8601, free-form natural date, or
  relative. Examples: 2023-10-04, last quarter, Q3 2024
- department: org-chart department name.
  Examples: HR, Engineering, Finance, IT
- product: product name, product id (ASIN), or product handle.
  Examples: B0BQ3K23Y1, Coffee Frother

OUTPUT SCHEMA (multi-task forward pass):
{
  "classifications": {
    "intent": [{"label": "<one of lookup|search|analytical|ambiguous>",
                "score": <float in [0,1]>}]
  },
  "entities": [
    {"label": "<one of the 6 types>", "text": "<verbatim span>",
     "score": <float in [0,1]>}
  ]
}

DECISION RULES (worked examples):
* "send a message to emp_1002 about the launch" → intent=lookup,
  entities=[{label:emp_id, text:emp_1002}]
* "who handles billing escalations on the customer success team" →
  intent=search, entities possibly empty
* "how many tickets did the IT team close last quarter" →
  intent=analytical, entities=[{label:department, text:IT},
                                {label:date, text:last quarter}]
* "help" → intent=ambiguous, entities=[]
* "status of CLNT-0042" → intent=lookup,
  entities=[{label:customer_id, text:CLNT-0042}]
* "compare sales for emp_0424 and emp_0728 in Q3" →
  intent=analytical, entities=both emp_ids and date span Q3
* "delete the complaint by Customer ID: bolid" → intent=lookup,
  entities=[{label:customer_id, text:bolid}]

CONSTRAINTS:
- Latency budget: 200 ms p95 on CPU.
- Deterministic: same query → same output. No sampling.
- Never emit prose. Output is consumed by code.

------ END SYSTEM PROMPT ------
````

### Message 2 — upload `seed_examples.jsonl`

Drag the file into the Pioneer chat. Add this short note:

```
Attaching seed_examples.jsonl — 170 examples, JSONL.
Format: {"query": "...", "intent": "...", "entities": {"<type>": ["<span>", ...]}}
Distribution: 150 lookup / 8 search / 8 analytical / 4 ambiguous
(intentionally lookup-heavy — please rebalance during synth expansion as
described in Message 1).
```

### Message 3 — upload `eval_set.jsonl`

Drag `pioneer/seeds/eval_set.jsonl` into the chat. Add:

```
Attaching eval_set.jsonl — 45 held-out examples, same format.
Use this for the baseline eval BEFORE the fine-tune, then again
post-fine-tune. Distribution: 30 lookup / 7 search / 4 analytical / 4
ambiguous.

Note: please load it from the uploaded file path the same way you load
the seed file. Do not inline-string-embed it in a Python heredoc — the
customer names contain apostrophes which breaks bash quoting.
```

### Message 4 — gate the fine-tune on baseline approval

```
After both uploads land, please:

1. Validate the seed/eval format and schema match the system prompt.
2. Run the baseline eval on eval_set.jsonl:
   - Column A: base GLiNER2 (no fine-tune)
   - Column B: Pioneer-hosted GPT-4o using the same system prompt
   Capture: intent accuracy, per-entity F1 (all 6 types), macro NER F1,
   p95 latency.
3. Send the 2-column table back to me. STOP and wait for my OK before
   kicking off the fine-tune.

If GPT-4o is well below 0.85 macro F1 the schema/prompt may need
tightening. If base GLiNER2 is already at 0.85+ the eval set is too easy
and we should expand it before training.
```

---

## What happens next

Pioneer agent runs the baseline. You send the table to me; I check it.
If green:

1. Tell Pioneer to proceed with the synth expansion (target 800 examples,
   rebalanced) + fine-tune (5 epochs).
2. After ~20 min, Pioneer ships the trained model + post-fine-tune eval.
3. Download weights → `pioneer/weights/<model-name>/` (gitignored).
4. Run local cross-check:
   ```
   uv add gliner
   GLINER2_MODEL_PATH=pioneer/weights/<model-name> \
     uv run python -m pioneer.bench.run_local_eval --name finetuned
   ```
5. Fill `pioneer/bench/results/comparison.md` with the 3-column table.
6. Side-challenge submission package — see the comparison.md footer.
