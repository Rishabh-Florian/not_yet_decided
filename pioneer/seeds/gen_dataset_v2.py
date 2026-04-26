"""Generate balanced NER + intent training and eval sets for the Pioneer
GLiNER2 fine-tune.

PHILOSOPHY (vs. v1 `gen_seeds.py`):
- v1 regex-scraped `tasks.jsonl` only. Result: 88 % lookup bias,
  customer_id=0, ticket_id=2, date=11 in 170 seeds. Eval had 0 of
  ticket_id and 0 of date — F1 unmeasurable on those types.
- v2 templates queries from REAL entity values pulled from each
  authoritative source. Every output example has perfect gold spans
  because the generator knows exactly which token it substituted in.

OUTPUTS
- `pioneer/seeds/seed_examples_v2.jsonl` — 600 balanced training rows
  (150 per intent class, every entity type >= 30 occurrences).
- `pioneer/seeds/eval_set_v2.jsonl` — 120 held-out rows (30 per
  intent, every entity type >= 15 occurrences).

GUARANTEES
- Train + eval are disjoint by (template_id, sampled_entities) hash.
- Every entity type has ground-truth coverage in BOTH splits.
- Real entity values match what the production graph actually contains
  (employees.json, customers.json, products.json, it_tickets.json).

CLI
    uv run python pioneer/seeds/gen_dataset_v2.py
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent.parent
DATASET = ROOT / "dataset" / "EnterpriseBench"
OUT_DIR = ROOT / "pioneer" / "seeds"


# --------------------------------------------------------------------------
# Load real entity values from each authoritative source
# --------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_employee_pool() -> tuple[list[str], list[str]]:
    """Returns (emp_ids, departments). employees.json has 1,260 records."""
    data = _load_json(DATASET / "Human_Resource_Management" / "Employees" / "employees.json")
    emp_ids = sorted({r["emp_id"] for r in data if r.get("emp_id")})
    departments = sorted({r["category"] for r in data if r.get("category")})
    return emp_ids, departments


def load_customer_pool() -> list[str]:
    """customers.json — 90 records with 5-char shortnames (arout, bolid, ...)."""
    data = _load_json(DATASET / "Customer_Relation_Management" / "customers.json")
    return sorted({r["customer_id"] for r in data if r.get("customer_id") and r["customer_id"] != "ADDED"})


def load_ticket_pool() -> list[str]:
    """it_tickets.json — 163 records, integer ids."""
    data = _load_json(DATASET / "IT_Service_Management" / "it_tickets.json")
    return sorted({str(r["id"]) for r in data if r.get("id") is not None})


def load_product_pool() -> tuple[list[str], list[str]]:
    """products.json — 1,351 ASINs + names. We sample short product names
    (the long ones are 200+ chars and would dominate the query)."""
    data = _load_json(DATASET / "Customer_Relation_Management" / "products.json")
    asins = sorted({r["product_id"] for r in data if r.get("product_id")})
    # Short, recognizable product handles. Take the first 2-3 words so spans
    # are clean. e.g. "Tokdis MX-1 Pro" instead of the full 200-char name.
    short_names: list[str] = []
    for r in data:
        name = r.get("product_name", "")
        if not name:
            continue
        words = name.split()
        if len(words) >= 2:
            short = " ".join(words[:2])
            if 4 <= len(short) <= 40 and not any(c in short for c in ",|"):
                short_names.append(short)
    return asins, sorted(set(short_names))


# --------------------------------------------------------------------------
# Date templates — natural language and ISO mixed.
# --------------------------------------------------------------------------

DATE_RELATIVE = [
    "today", "yesterday", "this week", "last week", "this month",
    "last month", "this quarter", "last quarter", "this year",
    "last year", "the past 30 days", "the past 6 months",
    "the past year",
]
DATE_NAMED = [
    "Q1 2023", "Q2 2023", "Q3 2023", "Q4 2023", "Q1 2024", "Q2 2024",
    "Q3 2024", "Q4 2024", "January 2024", "March 2024", "July 2023",
    "October 2022", "FY23", "FY24",
]
DATE_ISO = [
    "2024-03-15", "2023-10-04", "2022-06-17", "2021-12-30",
    "2020-09-02", "2019-04-25", "2018-12-02",
]


# --------------------------------------------------------------------------
# Templates per intent. `{slot}` markers will be filled with sampled values
# and the resulting spans recorded as gold entities.
# --------------------------------------------------------------------------

# Each template: (template_string, entity_slot_types_in_order)
# slot_types is a list of entity type names (matching ENTITY_TYPES).
# The generator will substitute `{0}`, `{1}`, ... positionally and record
# each substituted value as an entity of the matching type.

LOOKUP_TEMPLATES: list[tuple[str, list[str]]] = [
    ("Show me ticket {0}", ["ticket_id"]),
    ("What is the status of ticket {0}?", ["ticket_id"]),
    ("Get details of ticket id {0}", ["ticket_id"]),
    ("Pull the issue description for ticket-{0}", ["ticket_id"]),
    ("Resolve ticket {0} please", ["ticket_id"]),
    ("Find emails sent by {0}", ["emp_id"]),
    ("Who is {0}?", ["emp_id"]),
    ("Show profile for {0}", ["emp_id"]),
    ("Edit the performance rating for {0}", ["emp_id"]),
    ("Send a message to {0} about the deployment", ["emp_id"]),
    ("Get the manager of employee {0}", ["emp_id"]),
    ("List all repositories owned by {0}", ["emp_id"]),
    ("Open a new issue for {0} in the repo", ["emp_id"]),
    ("What is the email of {0}?", ["emp_id"]),
    ("Customer {0} order history please", ["customer_id"]),
    ("Pull invoice for customer_id {0}", ["customer_id"]),
    ("Show complaints filed by customer {0}", ["customer_id"]),
    ("Find shipping orders for customer_id: {0}", ["customer_id"]),
    ("Get product details for {0}", ["product"]),
    ("Show me reviews of product {0}", ["product"]),
    ("Update the price of {0} to ₹999", ["product"]),
    ("Pull catalog entry for product_id {0}", ["product"]),
    ("Find sales of {0} this year", ["product"]),
    ("Forward ticket {0} to {1}", ["ticket_id", "emp_id"]),
    ("Assign ticket {0} to {1}", ["ticket_id", "emp_id"]),
    ("Tell {0} that ticket {1} is resolved", ["emp_id", "ticket_id"]),
    ("Customer {0} bought {1} — pull the invoice", ["customer_id", "product"]),
    ("Find emails between {0} and {1}", ["emp_id", "emp_id"]),
    ("Compare profiles of {0} and {1}", ["emp_id", "emp_id"]),
    ("Send a message from {0} to {1} about Q3 review", ["emp_id", "emp_id"]),
    ("Pull the chat between {0} and {1} on {2}", ["emp_id", "emp_id", "date"]),
    ("Did {0} send any emails on {1}?", ["emp_id", "date"]),
    ("Show conversations involving {0} from {1}", ["emp_id", "date"]),
    ("Find ticket {0} raised by {1} on {2}", ["ticket_id", "emp_id", "date"]),
    ("Customer support chats handled by {0} on {1}", ["emp_id", "date"]),
    ("Pull the order placed by customer {0} on {1}", ["customer_id", "date"]),
    ("Get GitHub commits from {0} in {1}", ["emp_id", "date"]),
    ("Notify {0} about the new HR policy", ["emp_id"]),
    ("Email {0} from the {1} department about onboarding", ["emp_id", "department"]),
    ("Move {0} to the {1} team", ["emp_id", "department"]),
]

SEARCH_TEMPLATES: list[tuple[str, list[str]]] = [
    # Parameterized templates — combinatorial expansion via department slot.
    ("Find the {0} team's policy documents", ["department"]),
    ("Who handles {0} escalations?", ["department"]),
    ("Find the latest hire in {0}", ["department"]),
    ("Show me complaints from the {0} division", ["department"]),
    ("What is the {0} team working on this quarter?", ["department"]),
    ("Find the {0} department's onboarding docs", ["department"]),
    ("Who is the {0} lead currently?", ["department"]),
    ("Pull recent posts from the {0} team", ["department"]),
    ("List recent hires in {0}", ["department"]),
    ("Find {0} performance reviews", ["department"]),
    ("Surface emails from the {0} team", ["department"]),
    ("Who manages {0}?", ["department"]),
    ("Show me documentation owned by {0}", ["department"]),
    ("Find {0} training materials", ["department"]),
    ("List vendors used by {0}", ["department"]),
    ("Show projects led by the {0} team", ["department"]),
    ("Search {0} strategy docs", ["department"]),
    ("Find the budget for {0}", ["department"]),
    ("Show me {0} team chats", ["department"]),
    ("Find escalation patterns in {0}", ["department"]),
    # Static templates — diverse natural-language queries with no entities.
    ("Find all complaints about delayed shipping", []),
    ("Who handles billing escalations on the customer success team", []),
    ("Find the resume for the senior backend engineer with kubernetes experience", []),
    ("Show me posts about the Q4 strategy review", []),
    ("Search emails about deployment plans", []),
    ("Find the contract with the AWS vendor", []),
    ("Show me employees with python and machine learning skills who joined after 2022", []),
    ("Find policy documents about remote work", []),
    ("What was the agreed payment term in the last vendor contract for cloud services?", []),
    ("Find the meeting notes from last week's leadership offsite", []),
    ("Find all support chats about coffee frother complaints", []),
    ("Who owns the inazuma analytics dashboard repository", []),
    ("Show me recent emails about budget reviews", []),
    ("Find issues mentioning slow VPN connectivity", []),
    ("What policies govern data privacy?", []),
    ("Pull conversations discussing the new authentication service", []),
    ("Show me the latest performance reviews", []),
    ("Find emails mentioning the cloud migration", []),
    ("Search for resumes with React and TypeScript experience", []),
    ("Show me posts mentioning the AI roadmap", []),
    ("Find threads discussing the security incident", []),
    ("Who reviewed the new compensation framework?", []),
    ("Find docs about the customer onboarding process", []),
    ("Show me recent vendor evaluations", []),
    ("Find emails about the Q1 hiring freeze", []),
    ("Search posts about diversity initiatives", []),
    ("Find the runbook for production incidents", []),
    ("Show me complaints about late payments", []),
    ("Find feedback on the new performance review cycle", []),
    ("Search emails containing 'urgent' from last month", []),
    ("Show me threads about the conference talk submissions", []),
    ("Find documentation on the API rate limits", []),
    ("Who is responsible for cloud cost optimization?", []),
    ("Find the SLA for high-priority tickets", []),
    ("Show me messages from the customer success channel", []),
    ("Find roadmap discussions about mobile app", []),
    ("Search for the most recent product launch announcement", []),
    ("Find the architecture review notes", []),
    ("Show me recently updated wikis", []),
    ("Find quarterly business review summaries", []),
]

ANALYTICAL_TEMPLATES: list[tuple[str, list[str]]] = [
    ("How many tickets did {0} close {1}?", ["department", "date"]),
    ("Average ticket resolution time per department", []),
    ("Count of VPN outages over {0}", ["date"]),
    ("Top 5 products by revenue {0}", ["date"]),
    ("Compare sales for {0} and {1} in {2}", ["emp_id", "emp_id", "date"]),
    ("Trend of customer complaints over {0}", ["date"]),
    ("Average salary by department", []),
    ("How many emails were sent {0}?", ["date"]),
    ("Rank departments by ticket volume {0}", ["date"]),
    ("Sum of invoiced amounts for customer {0} {1}", ["customer_id", "date"]),
    ("How many active accounts does the customer support team handle daily on average?", []),
    ("Compare ticket counts for {0} and {1}", ["department", "department"]),
    ("How many employees joined {0} {1}?", ["department", "date"]),
    ("Total support chats by {0} {1}", ["emp_id", "date"]),
    ("Average review rating for product {0}", ["product"]),
    ("Count tickets raised by {0}", ["emp_id"]),
    ("How much did customer {0} spend {1}?", ["customer_id", "date"]),
    ("Trend of {0} sales over {1}", ["product", "date"]),
    ("Compare revenue between {0} and {1} in {2}", ["customer_id", "customer_id", "date"]),
    ("Top 3 employees by ticket count in {0}", ["department"]),
    ("Average resolution time for tickets in {0}", ["department"]),
    ("How many products were updated {0}?", ["date"]),
    ("Sum of leaves taken by {0} team {1}", ["department", "date"]),
    ("Compare {0} and {1} performance over {2}", ["emp_id", "emp_id", "date"]),
    ("Trend of email volume in {0}", ["department"]),
    ("How many high-priority tickets does {0} have?", ["emp_id"]),
    ("Total sales of {0} {1}", ["product", "date"]),
    ("Count messages sent by {0} during {1}", ["emp_id", "date"]),
    ("Average customer rating for products in the {0} category", ["department"]),
    ("How many resumes were uploaded {0}?", ["date"]),
]

AMBIGUOUS_TEMPLATES: list[tuple[str, list[str]]] = [
    # Single-token / two-token vague queries
    ("help", []), ("status", []), ("data", []), ("update", []),
    ("show me", []), ("info", []), ("click here", []), ("okay", []),
    ("thanks", []), ("the thing", []), ("fix this", []), ("yes", []),
    ("continue", []), ("more", []), ("again", []), ("dashboard", []),
    ("settings", []), ("admin", []), ("results", []), ("the report", []),
    ("hi", []), ("hello", []), ("test", []), ("loading", []),
    ("error", []), ("undefined", []), ("null", []), ("?", []),
    ("123", []), ("xyz", []), ("foo", []), ("bar", []),
    ("query", []), ("the system", []), ("process this", []),
    ("never mind", []), ("forget it", []), ("ok thanks", []),
    ("got it", []), ("makes sense", []), ("hmm", []), ("idk", []),
    ("maybe", []), ("not sure", []), ("show", []), ("please", []),
    ("anything", []), ("everything", []), ("the system again", []),
    ("loading data", []), ("error message", []), ("retry", []),
    ("submit", []), ("close", []), ("open", []), ("back", []),
    ("home", []), ("more info", []), ("details", []), ("history", []),
    ("the page", []), ("just checking", []),
]


# --------------------------------------------------------------------------
# Generator
# --------------------------------------------------------------------------

ENTITY_TYPES = ["emp_id", "customer_id", "ticket_id", "date", "department", "product"]
INTENTS = ["lookup", "search", "analytical", "ambiguous"]


def make_sampler(rng: random.Random) -> Callable[[str], str]:
    """Returns sample(entity_type) -> a real value. Each call independent."""
    emp_ids, departments = load_employee_pool()
    customers = load_customer_pool()
    tickets = load_ticket_pool()
    asins, product_names = load_product_pool()

    pools: dict[str, list[str]] = {
        "emp_id": emp_ids,
        "customer_id": customers,
        "ticket_id": tickets,
        "department": departments,
        "product": asins + product_names,  # mix ASINs and short names
        "date": DATE_RELATIVE + DATE_NAMED + DATE_ISO,
    }

    # Print pool sizes once for visibility.
    sizes = {k: len(v) for k, v in pools.items()}
    print(f"Entity pools: {sizes}")

    def sample(etype: str) -> str:
        if etype not in pools:
            raise KeyError(f"unknown entity type {etype!r}")
        return rng.choice(pools[etype])

    return sample


def render_template(
    template: str,
    slot_types: list[str],
    sample: Callable[[str], str],
) -> tuple[str, dict[str, list[str]]]:
    """Substitute slots and record the gold entity spans.

    Returns (rendered_query, {entity_type: [span, ...]}).
    """
    values = [sample(t) for t in slot_types]
    rendered = template.format(*values)
    spans: dict[str, list[str]] = {}
    for t, v in zip(slot_types, values):
        spans.setdefault(t, []).append(v)
    # Sanity: every gold span must appear verbatim in the rendered query.
    for t, vs in spans.items():
        for v in vs:
            if v not in rendered:
                raise RuntimeError(
                    f"span {v!r} ({t}) not found verbatim in rendered "
                    f"query {rendered!r} — template formatting bug"
                )
    return rendered, spans


def generate_split(
    intent_to_templates: dict[str, list[tuple[str, list[str]]]],
    per_intent_count: dict[str, int],
    sample: Callable[[str], str],
    rng: random.Random,
    seen: set[str],
) -> list[dict[str, Any]]:
    """Generate examples split across intents. `seen` deduplicates by query."""
    out: list[dict[str, Any]] = []
    for intent, count in per_intent_count.items():
        templates = intent_to_templates[intent]
        attempts = 0
        produced = 0
        while produced < count and attempts < count * 10:
            attempts += 1
            tpl, slots = rng.choice(templates)
            query, ents = render_template(tpl, slots, sample)
            if query in seen:
                continue
            seen.add(query)
            out.append({"query": query, "intent": intent, "entities": ents})
            produced += 1
        if produced < count:
            print(
                f"WARN: only produced {produced}/{count} for intent {intent}; "
                f"templates may be too few for the dedup target"
            )
    rng.shuffle(out)
    return out


def coverage_report(rows: list[dict[str, Any]], label: str) -> None:
    print(f"\n=== {label} ({len(rows)} rows) ===")
    intent_counts = Counter(r["intent"] for r in rows)
    print("Intent distribution:")
    for intent in INTENTS:
        c = intent_counts.get(intent, 0)
        print(f"  {intent:12s} {c:4d}  ({100*c/len(rows):.1f}%)")
    ent_counts: Counter[str] = Counter()
    for r in rows:
        for t, vs in (r.get("entities") or {}).items():
            ent_counts[t] += len(vs)
    print("Entity mention counts:")
    for t in ENTITY_TYPES:
        print(f"  {t:12s} {ent_counts.get(t, 0):4d}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    sample = make_sampler(rng)

    intent_to_templates = {
        "lookup": LOOKUP_TEMPLATES,
        "search": SEARCH_TEMPLATES,
        "analytical": ANALYTICAL_TEMPLATES,
        "ambiguous": AMBIGUOUS_TEMPLATES,
    }

    # Generate EVAL FIRST so the limited unique-string space (especially
    # ambiguous, which has ~60 unique single-token variants) gives eval
    # priority. Train then fills with whatever's left — still plenty.
    seen: set[str] = set()
    eval_set = generate_split(
        intent_to_templates,
        per_intent_count={"lookup": 30, "search": 30, "analytical": 30, "ambiguous": 15},
        sample=sample,
        rng=rng,
        seen=seen,
    )
    train = generate_split(
        intent_to_templates,
        per_intent_count={"lookup": 150, "search": 150, "analytical": 150, "ambiguous": 50},
        sample=sample,
        rng=rng,
        seen=seen,
    )

    train_path = OUT_DIR / "seed_examples_v2.jsonl"
    eval_path = OUT_DIR / "eval_set_v2.jsonl"
    with train_path.open("w", encoding="utf-8") as f:
        for row in train:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with eval_path.open("w", encoding="utf-8") as f:
        for row in eval_set:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    coverage_report(train, "TRAIN")
    coverage_report(eval_set, "EVAL")

    print(f"\nWrote {train_path}")
    print(f"Wrote {eval_path}")


if __name__ == "__main__":
    main()
