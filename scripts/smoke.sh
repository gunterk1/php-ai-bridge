#!/usr/bin/env bash
# End-to-end smoke test: health -> ingest the sample doc -> ask a question.
# Usage: scripts/smoke.sh [base_url]   (default http://localhost:8080)
set -euo pipefail

BASE="${1:-http://localhost:8080}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "== health =="
curl -sf "$BASE/api/health"; echo

echo "== ingest =="
# Encode the sample file as a JSON body without needing jq.
python3 - "$ROOT/examples/sample-doc.txt" <<'PY' | curl -sf -X POST "$BASE/api/ingest" \
  -H 'Content-Type: application/json' --data-binary @-
import json, sys
text = open(sys.argv[1], encoding="utf-8").read()
print(json.dumps({"doc_id": "sample", "text": text}))
PY
echo

echo "== query =="
curl -sf -X POST "$BASE/api/query" \
  -H 'Content-Type: application/json' \
  -d '{"question": "How does the PHP app talk to the model, and can it run locally?"}'
echo
