#!/usr/bin/env python3
"""Run the labelled evaluation set against a live ai-service and print a scorecard.

The runner talks to the service over the same three REST endpoints the applications
use. That is the point: it measures the system as deployed, including the retriever
configuration and the provider currently wired up, rather than reaching into the
engine and testing something the users never touch.

    python -m eval.run_eval                        # against http://localhost:8000
    python -m eval.run_eval --base-url http://…    # somewhere else
    python -m eval.run_eval --k 6 --json out.json  # different top-k, machine-readable

Exit code is 1 if any hard invariant is violated — an invented citation, or an
answered question that should have been refused. Retrieval quality is reported but
does not fail the run: it is a number to watch over time, not a pass/fail gate.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from .metrics import check_citations, classify_abstention, mean, recall_at_k, reciprocal_rank

HERE = Path(__file__).parent
CORPUS = HERE / "corpus"
DATASET = HERE / "dataset.yaml"


def post(base_url: str, path: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def get(base_url: str, path: str, timeout: float) -> dict:
    with urllib.request.urlopen(f"{base_url.rstrip('/')}{path}", timeout=timeout) as resp:
        return json.loads(resp.read())


def ingest_corpus(base_url: str, timeout: float) -> list[str]:
    """Ingest every corpus document under its filename stem. Returns the doc ids."""
    ids = []
    for path in sorted(CORPUS.glob("*.md")):
        doc_id = path.stem
        post(base_url, "/ingest", {"doc_id": doc_id, "text": path.read_text()}, timeout)
        ids.append(doc_id)
    return ids


def evaluate(base_url: str, k: int, timeout: float) -> dict:
    dataset = yaml.safe_load(DATASET.read_text())
    rows = []

    for case in dataset.get("answerable", []):
        result = post(base_url, "/query", {"question": case["question"], "k": k}, timeout)
        retrieved = result.get("sources", [])
        answer = result.get("answer", "")
        citations = check_citations(answer, retrieved)
        rows.append(
            {
                "id": case["id"],
                "kind": "answerable",
                "question": case["question"],
                "expected": case["expected_sources"],
                "retrieved": retrieved,
                "recall": recall_at_k(case["expected_sources"], retrieved),
                "rr": reciprocal_rank(case["expected_sources"], retrieved),
                "verdict": classify_abstention(answer),
                "cited": list(citations.cited),
                "invented": list(citations.invented),
                "answer": answer,
            }
        )

    for case in dataset.get("unanswerable", []):
        result = post(base_url, "/query", {"question": case["question"], "k": k}, timeout)
        retrieved = result.get("sources", [])
        answer = result.get("answer", "")
        citations = check_citations(answer, retrieved)
        rows.append(
            {
                "id": case["id"],
                "kind": "unanswerable",
                "question": case["question"],
                "why": case.get("why", ""),
                "retrieved": retrieved,
                "verdict": classify_abstention(answer),
                "cited": list(citations.cited),
                "invented": list(citations.invented),
                "answer": answer,
            }
        )

    answerable = [r for r in rows if r["kind"] == "answerable"]
    unanswerable = [r for r in rows if r["kind"] == "unanswerable"]

    # Hard invariants. An invented citation is a correctness bug in any mode. An
    # unanswerable question that gets answered is the promise in the system prompt
    # being broken, which is the whole reason this dataset exists.
    invented = [r["id"] for r in rows if r["invented"]]
    unwarranted = [r["id"] for r in unanswerable if r["verdict"] == "answered"]

    summary = {
        "k": k,
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "recall_at_k": round(mean([r["recall"] for r in answerable]), 3),
        "mrr": round(mean([r["rr"] for r in answerable]), 3),
        "abstention_rate": round(
            mean([1.0 if r["verdict"] == "abstained" else 0.0 for r in unanswerable]), 3
        ),
        "answered_when_unanswerable": unwarranted,
        "invented_citations": invented,
        "unclear_verdicts": [r["id"] for r in rows if r["verdict"] == "unclear"],
    }
    return {"summary": summary, "rows": rows}


def render(report: dict) -> None:
    s = report["summary"]
    print()
    print("  RAG evaluation")
    print("  " + "-" * 58)
    print(f"  top-k                      {s['k']}")
    print(f"  answerable questions       {s['answerable']}")
    print(f"  unanswerable questions     {s['unanswerable']}")
    print()
    print(f"  recall@k                   {s['recall_at_k']:.3f}   (retrieval found the labelled chunk)")
    print(f"  MRR                        {s['mrr']:.3f}   (…and how early in the list)")
    print(f"  abstention rate            {s['abstention_rate']:.3f}   (refused when it should)")
    print()

    if s["invented_citations"]:
        print(f"  FAIL  invented citations:        {', '.join(s['invented_citations'])}")
    else:
        print("  ok    every citation refers to a retrieved chunk")

    if s["answered_when_unanswerable"]:
        print(f"  FAIL  answered anyway:           {', '.join(s['answered_when_unanswerable'])}")
    else:
        print("  ok    no unanswerable question was answered")

    if s["unclear_verdicts"]:
        print(f"  note  needs a human look:        {', '.join(s['unclear_verdicts'])}")
    print()

    misses = [r for r in report["rows"] if r["kind"] == "answerable" and r["recall"] < 1.0]
    if misses:
        print("  Retrieval misses")
        for r in misses:
            print(f"    {r['id']}  expected {r['expected']}  got {r['retrieved']}")
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--json", metavar="PATH", help="also write the full report as JSON")
    args = ap.parse_args()

    try:
        health = get(args.base_url, "/health", args.timeout)
    except (urllib.error.URLError, OSError) as exc:
        print(f"ai-service not reachable at {args.base_url}: {exc}", file=sys.stderr)
        print("start it with `docker compose up` or `uvicorn app.main:app --port 8000`", file=sys.stderr)
        return 2

    print(f"  service: {args.base_url}  provider: {health.get('provider', 'unknown')}")
    ingested = ingest_corpus(args.base_url, args.timeout)
    print(f"  corpus:  {len(ingested)} documents ({', '.join(ingested)})")

    report = evaluate(args.base_url, args.k, args.timeout)
    render(report)

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"  full report written to {args.json}\n")

    s = report["summary"]
    return 1 if s["invented_citations"] or s["answered_when_unanswerable"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
