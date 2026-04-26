# Archive — v1 seeds (superseded)

These were the original Round 1 + Round 2 training/eval files, derived
by regex-scraping `dataset/EnterpriseBench/tasks.jsonl` via
`gen_seeds.py`. They worked for intent classification (v2 hit 0.978
intent acc) but the NER head got stuck at macro F1 ~0.4 because:

- `tasks.jsonl` is 88 % command-style, so seeds had only 2 ticket_id, 11
  date, 0 customer_id mentions.
- The eval set had **0** ground-truth examples for ticket_id, date, AND
  customer_id — three of six entity types couldn't even be measured.

Replaced by `pioneer/seeds/gen_dataset_v2.py` which **templates queries
from real graph entity values** (employees.json, customers.json,
it_tickets.json, products.json). Every gold span is correct by
construction; every entity type has measurable eval coverage.

Kept here for reproducibility / before-vs-after comparisons. Do not use
for new fine-tunes — the v2 dataset (one folder up) is the production
source.
