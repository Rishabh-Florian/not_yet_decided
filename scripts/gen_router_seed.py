"""Generate `seed_examples.jsonl` and `eval_set.jsonl` for the
Pioneer.ai GLiNER2 fine-tune from EnterpriseBench tasks.

This is a one-shot derivation script. Re-run after the entity-type
schema changes in `backend/retrieval/router.py::ENTITY_TYPES`.

Output goes to `backend/retrieval/router_train/{seed_examples,eval_set}.jsonl`.

Format (one JSON object per line):

    {
      "query": "<user message>",
      "intent": "lookup|search|analytical|ambiguous",
      "entities": {"emp_id": ["emp_1002"], ...}
    }

The `lookup`/`search` distinction is heuristic (id-shaped tokens ->
lookup; otherwise -> search). The `analytical` and `ambiguous` examples
are hand-curated because they do not appear naturally in
EnterpriseBench's command-style tasks. After Pioneer.ai produces
synthetic data from these seeds, the human reviewer should up-weight
the under-represented intents to balance the training set.
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "dataset" / "EnterpriseBench" / "tasks.jsonl"
OUT_DIR = ROOT / "backend" / "retrieval" / "router_train"

PAT_EMP = re.compile(r"\bemp_\d+\b")
PAT_CLNT = re.compile(r"\b(?:CLNT|CUST|VEND|ORG)-\d+\b")
PAT_ASIN = re.compile(r"\b[A-Z][0-9A-Z]{9}\b")
PAT_TICKET_ID = re.compile(r"\bTicket\s+id\s+(\d+)\b", re.IGNORECASE)
PAT_TICKET_DASH = re.compile(r"\bticket[-_:]\d+\b", re.IGNORECASE)
PAT_DEPT = re.compile(
    r"\b(HR|Engineering|Sales|Finance|Marketing|Legal|Operations|Human Resources|IT|Product)\b"
)
PAT_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def extract(q: str) -> dict[str, list[str]]:
    ents: dict[str, list[str]] = {}
    emps = list(dict.fromkeys(PAT_EMP.findall(q)))
    if emps:
        ents["emp_id"] = emps[:6]
    cls = list(dict.fromkeys(PAT_CLNT.findall(q)))
    if cls:
        ents["customer_id"] = cls[:3]
    asins = list(dict.fromkeys(PAT_ASIN.findall(q)))
    if asins:
        ents["product"] = asins[:3]
    tids: list[str] = []
    for m in PAT_TICKET_ID.finditer(q):
        tids.append(m.group(1))
    for m in PAT_TICKET_DASH.finditer(q):
        tids.append(m.group(0))
    if tids:
        ents["ticket_id"] = list(dict.fromkeys(tids))[:3]
    depts = list(dict.fromkeys(PAT_DEPT.findall(q)))
    if depts:
        ents["department"] = depts[:3]
    dates = list(dict.fromkeys(PAT_DATE.findall(q)))
    if dates:
        ents["date"] = dates[:3]
    return ents


# Hand-curated examples for under-represented intents.
SEARCH_EXAMPLES = [
    {"query": "who handles billing escalations on the customer success team",
     "intent": "search", "entities": {}},
    {"query": "find the resume for the senior backend engineer with kubernetes experience",
     "intent": "search", "entities": {}},
    {"query": "what was the agreed payment term in the last vendor contract for cloud services",
     "intent": "search", "entities": {}},
    {"query": "show me the latest performance review for the head of finance",
     "intent": "search", "entities": {"department": ["Finance"]}},
    {"query": "employees with python and machine learning skills who joined after 2022",
     "intent": "search", "entities": {}},
    {"query": "send a message to Anil Rathore regarding the new product launch timeline",
     "intent": "search", "entities": {}},
    {"query": "find all customer complaints about delayed shipping in the last quarter",
     "intent": "search", "entities": {}},
    {"query": "find the IT ticket about VPN connectivity from last week",
     "intent": "search", "entities": {"department": ["IT"]}},
    {"query": "summarize the recent emails about the budget review with the finance team",
     "intent": "search", "entities": {"department": ["Finance"]}},
    {"query": "who owns the inazuma analytics dashboard repository",
     "intent": "search", "entities": {}},
    {"query": "what is our current policy on remote work for the engineering team",
     "intent": "search", "entities": {"department": ["Engineering"]}},
    {"query": "show all support chats about the Coffee Frother product complaints",
     "intent": "search", "entities": {}},
    {"query": "what did the HR director say about the new performance review process",
     "intent": "search", "entities": {"department": ["HR"]}},
    {"query": "what items are pending review by the legal team this week",
     "intent": "search", "entities": {"department": ["Legal"]}},
    {"query": "who is the latest hire in marketing",
     "intent": "search", "entities": {"department": ["Marketing"]}},
]
ANALYTICAL_EXAMPLES = [
    {"query": "How many tickets did the IT team close last quarter?",
     "intent": "analytical", "entities": {"department": ["IT"]}},
    {"query": "count of vpn outages over the last 30 days",
     "intent": "analytical", "entities": {}},
    {"query": "Average ticket resolution time per department this year",
     "intent": "analytical", "entities": {}},
    {"query": "Compare sales for emp_0424 and emp_0728 in Q3",
     "intent": "analytical", "entities": {"emp_id": ["emp_0424", "emp_0728"]}},
    {"query": "Trend of customer complaints by product over the last 6 months",
     "intent": "analytical", "entities": {}},
    {"query": "How many employees joined HR in 2023?",
     "intent": "analytical", "entities": {"department": ["HR"]}},
    {"query": "What is the average salary by department?",
     "intent": "analytical", "entities": {}},
    {"query": "Rank departments by ticket volume this year",
     "intent": "analytical", "entities": {}},
    {"query": "How many unresolved high-priority tickets does emp_0990 have?",
     "intent": "analytical", "entities": {"emp_id": ["emp_0990"]}},
    {"query": "Sum of invoiced amounts for customer CLNT-0042 this year",
     "intent": "analytical", "entities": {"customer_id": ["CLNT-0042"]}},
    {"query": "How many active accounts does the customer support team handle daily on average",
     "intent": "analytical", "entities": {}},
    {"query": "Top 5 products by revenue last month",
     "intent": "analytical", "entities": {}},
]
AMBIGUOUS_EXAMPLES = [
    {"query": "help", "intent": "ambiguous", "entities": {}},
    {"query": "status", "intent": "ambiguous", "entities": {}},
    {"query": "update", "intent": "ambiguous", "entities": {}},
    {"query": "fix the thing", "intent": "ambiguous", "entities": {}},
    {"query": "data", "intent": "ambiguous", "entities": {}},
    {"query": "show me", "intent": "ambiguous", "entities": {}},
    {"query": "thing", "intent": "ambiguous", "entities": {}},
    {"query": "click here", "intent": "ambiguous", "entities": {}},
]


def harvest_lookup_examples(limit: int) -> list[dict[str, object]]:
    """Pull command-style queries with id-shaped tokens from tasks.jsonl."""
    out: list[dict[str, object]] = []
    with TASKS.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i > 1500:
                break
            d = json.loads(line)
            msgs = d["messages"]
            user = next(
                (m["content"] for m in msgs if m.get("role") == "user" and m.get("content")),
                None,
            )
            if not user:
                continue
            user = user.strip()
            if len(user) > 350:
                user = user[:350].rsplit(" ", 1)[0] + "..."
            ents = extract(user)
            if not ents:
                continue
            if any(k in ents for k in ("emp_id", "customer_id", "ticket_id", "product")):
                out.append({"query": user, "intent": "lookup", "entities": ents})
                if len(out) >= limit:
                    break
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    lookups = harvest_lookup_examples(limit=70)

    seed = (
        lookups[:30]
        + SEARCH_EXAMPLES[:8]
        + ANALYTICAL_EXAMPLES[:8]
        + AMBIGUOUS_EXAMPLES[:4]
    )
    rng.shuffle(seed)
    eval_set = (
        lookups[30:60]
        + SEARCH_EXAMPLES[8:]
        + ANALYTICAL_EXAMPLES[8:]
        + AMBIGUOUS_EXAMPLES[4:]
    )
    rng.shuffle(eval_set)
    eval_set = eval_set[:50]

    seed_path = OUT_DIR / "seed_examples.jsonl"
    eval_path = OUT_DIR / "eval_set.jsonl"
    with seed_path.open("w", encoding="utf-8") as f:
        for item in seed:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    with eval_path.open("w", encoding="utf-8") as f:
        for item in eval_set:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"seed: {len(seed)} -> {seed_path}")
    print(f"  intents: {Counter(x['intent'] for x in seed)}")
    print(f"eval: {len(eval_set)} -> {eval_path}")
    print(f"  intents: {Counter(x['intent'] for x in eval_set)}")


if __name__ == "__main__":
    main()
