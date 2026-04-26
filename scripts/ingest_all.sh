#!/usr/bin/env bash
# Onboard + promote + run every EnterpriseBench source. Idempotent at every
# step: onboard skips when YAML exists, promote is a no-op when already
# active, run is record-level idempotent on (spec_version, source_file,
# source_record_id, content_hash).
#
# Usage:
#   bash scripts/ingest_all.sh                # all sources, then resolve-identity
#   bash scripts/ingest_all.sh emails posts   # only the named sources
#   FORCE_ONBOARD=1 bash scripts/ingest_all.sh employees   # re-draft spec
set -euo pipefail

TENANT="enterprisebench"
SPEC_DIR="ingest_specs/${TENANT}"
DATA_DIR="dataset/EnterpriseBench"
DB="data/better_context.sqlite"

# rel-path-under-DATA_DIR | spec-stem
# Order matters: employees first so subsequent sources MERGE Person nodes
# into the canonical employee records (same person:{emp_id} id template).
SOURCES=(
  "Human_Resource_Management/Employees/employees.json|employees"
  "Enterprise_mail_system/emails.json|emails"
  "Collaboration_tools/conversations.json|conversations"
  "Enterprise Social Platform/posts.json|posts"
  "IT_Service_Management/it_tickets.json|it_tickets"
  "Workspace/GitHub/GitHub.json|github"
  "Customer_Relation_Management/customers.json|customers"
  "Customer_Relation_Management/products.json|products"
  "Customer_Relation_Management/sales.json|sales"
  "Business_and_Management/clients.json|clients"
  "Business_and_Management/vendors.json|vendors"
  "Customer_Relation_Management/Customer Support/customer_support_chats.json|customer_support_chats"
  "Customer_Relation_Management/Product Sentiment/product_sentiment.json|product_sentiment"
  "Human_Resource_Management/Resume/resume_information.csv|resumes"
)

mkdir -p "$SPEC_DIR"

# Overwrite source.file_pattern in the drafted YAML with the canonical
# relative path so promote's --source-pattern always lines up.
normalize_file_pattern() {
  local spec="$1" rel_src="$2"
  uv run python - "$spec" "$rel_src" <<'PY'
import sys, yaml, pathlib
spec_path, rel_src = sys.argv[1], sys.argv[2]
data = yaml.safe_load(pathlib.Path(spec_path).read_text(encoding="utf-8"))
if data["source"].get("file_pattern") != rel_src:
    data["source"]["file_pattern"] = rel_src
    pathlib.Path(spec_path).write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
PY
}

ingest_one() {
  local rel_src="$1" name="$2"
  local src="${DATA_DIR}/${rel_src}"
  local spec="${SPEC_DIR}/${name}.yaml"

  echo "=== ${name} ==="

  if [[ ! -f "$src" ]]; then
    echo "  SKIP: source not found at ${src}"
    return
  fi

  if [[ -f "$spec" && "${FORCE_ONBOARD:-0}" != "1" ]]; then
    echo "  ONBOARD: skipped (spec exists at ${spec}; FORCE_ONBOARD=1 to redraft)"
  else
    if [[ -z "${GEMINI_API_KEY:-}" ]] && ! grep -q '^GEMINI_API_KEY=' .env 2>/dev/null; then
      echo "  ABORT: GEMINI_API_KEY not in env or .env" >&2
      exit 1
    fi
    echo "  ONBOARD: drafting spec via Gemini..."
    uv run python -m backend.ingest onboard "$src" \
      --tenant "$TENANT" --out "$spec" --db "$DB"
  fi

  normalize_file_pattern "$spec" "$rel_src"

  echo "  PROMOTE..."
  uv run python -m backend.ingest promote \
    --tenant "$TENANT" --source-pattern "$rel_src" --version 1 --db "$DB"

  echo "  RUN..."
  uv run python -m backend.ingest run "$spec" "$src" --db "$DB"
  echo
}

if [[ $# -gt 0 ]]; then
  for arg in "$@"; do
    matched=0
    for entry in "${SOURCES[@]}"; do
      rel="${entry%|*}"; name="${entry#*|}"
      if [[ "$name" == "$arg" ]]; then
        ingest_one "$rel" "$name"
        matched=1
        break
      fi
    done
    if [[ $matched -eq 0 ]]; then
      echo "no source named: $arg" >&2
      echo "available: $(printf '%s ' "${SOURCES[@]##*|}")" >&2
      exit 1
    fi
  done
else
  for entry in "${SOURCES[@]}"; do
    rel="${entry%|*}"; name="${entry#*|}"
    ingest_one "$rel" "$name"
  done

  echo "=== resolve-identity ==="
  uv run python -m backend.ingest resolve-identity --db "$DB"
fi
