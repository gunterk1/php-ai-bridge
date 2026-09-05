#!/usr/bin/env python3
"""Run the labelled set through the ADK agent, then score it twice: deterministically
and with ADK's LLM-as-judge.

The two scorers see byte-identical answers. Any disagreement is therefore a property of
the scorers, not of the run — which is the whole point. `ai-service/eval/metrics.py`
documents one case where its own abstention screen is known to be wrong (u5: an answer
that asserts something false, contradicts itself, then closes with "I do not know", and
is scored as a clean abstention). Its docstring says the fix "needs a reader, not a word
list". ADK ships that reader. This asks whether the reader available here is good enough
to be one.

    python evaluate.py                      # one pass, agent and judge on llama3.2:1b
    python evaluate.py --repeats 3          # judge stability across identical inputs
    python evaluate.py --judge-model ollama_chat/qwen2.5:0.5b
    python evaluate.py --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import yaml
from google.adk.evaluation.eval_case import Invocation
from google.adk.evaluation.eval_metrics import (
    EvalMetric,
    HallucinationsCriterion,
    JudgeModelOptions,
)
from google.adk.evaluation.hallucinations_v1 import HallucinationsV1Evaluator
from google.adk.runners import InMemoryRunner
from google.genai import types

import agent

HERE = Path(__file__).resolve().parent
EVAL_DIR = HERE.parent / "ai-service"
sys.path.insert(0, str(EVAL_DIR))
from eval.metrics import (  # noqa: E402  (path set above on purpose)
    check_citations,
    classify_abstention,
    mean,
    recall_at_k,
    reciprocal_rank,
)

DATASET = EVAL_DIR / "eval" / "dataset.yaml"


def load_cases() -> list[dict]:
    """Flatten dataset.yaml into one list, tagging each case with its half."""
    data = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    cases = []
    for case in data.get("answerable", []):
        cases.append({**case, "kind": "answerable"})
    for case in data.get("unanswerable", []):
        cases.append({**case, "kind": "unanswerable"})
    return cases


async def ask(runner: InMemoryRunner, question: str) -> tuple[str, list[str]]:
    """Put one question to the agent; return its answer and what it retrieved."""
    agent.reset_retrieval()
    session = await runner.session_service.create_session(app_name="adk-eval", user_id="u")
    parts: list[str] = []
    async for event in runner.run_async(
        user_id="u",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=question)]),
    ):
        if event.content and event.content.parts:
            parts.extend(p.text for p in event.content.parts if p.text)
    return " ".join(parts).strip(), agent.last_retrieved()


async def run_agent(cases: list[dict], model: str, k: int) -> list[dict]:
    """Answer every labelled question once, recording answer and retrieved ids."""
    runner = InMemoryRunner(agent=agent.build_agent(model), app_name="adk-eval")
    rows = []
    for case in cases:
        started = time.monotonic()
        answer, retrieved = await ask(runner, case["question"])
        rows.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "question": case["question"],
                "expected_sources": case.get("expected_sources", []),
                "answer": answer,
                "retrieved": retrieved,
                "seconds": round(time.monotonic() - started, 1),
            }
        )
        print(f"  {case['id']:>3}  {rows[-1]['seconds']:>5.1f}s  {answer[:60]!r}", flush=True)
    return rows


def score_deterministic(rows: list[dict], k: int) -> list[dict]:
    """Apply the existing metrics — the ones this repository already ships."""
    for row in rows:
        citations = check_citations(row["answer"], row["retrieved"])
        row["abstention"] = classify_abstention(row["answer"])
        row["invented_citations"] = list(citations.invented)
        row["cited"] = list(citations.cited)
        if row["kind"] == "answerable":
            row["recall_at_k"] = recall_at_k(row["expected_sources"], row["retrieved"])
            row["reciprocal_rank"] = reciprocal_rank(row["expected_sources"], row["retrieved"])
        # Three outcomes, not two. The first version of this collapsed "unclear" into
        # "pass" and let u5 through — an unanswerable question answered with the
        # invented claim "The PHP client performs 5 retries on a 404 error", scored
        # clean because the reply was too terse to classify as `answered`.
        #
        # metrics.py warns about exactly this: "'unclear' is the honest verdict ...
        # those rows belong in front of a human, not in an average." So they get their
        # own bucket instead of a silent pass.
        if row["invented_citations"] or (
            row["kind"] == "unanswerable" and row["abstention"] == "answered"
        ):
            row["deterministic_verdict"] = "fail"
        elif row["abstention"] == "unclear":
            row["deterministic_verdict"] = "review"
        else:
            row["deterministic_verdict"] = "pass"
    return rows


def to_invocation(row: dict) -> Invocation:
    """Wrap a recorded answer as an ADK Invocation the judge can read.

    The retrieved passages go into the user content alongside the question. The
    hallucination metric grades an answer against the context it was given, so the
    context has to travel with it — otherwise the judge is asked whether a claim is
    true in general, which is a different and much harder question.
    """
    context = "\n\n".join(
        f"[{c.chunk_id}]\n{c.text}" for c in agent.retrieve(row["question"], len(row["retrieved"]) or 4)
    )
    return Invocation(
        invocation_id=row["id"],
        user_content=types.Content(
            role="user",
            parts=[types.Part(text=f"Context:\n{context}\n\nQuestion: {row['question']}")],
        ),
        final_response=types.Content(role="model", parts=[types.Part(text=row["answer"] or "")]),
    )


async def score_judge(rows: list[dict], judge_model: str, samples: int) -> list[float | None]:
    """Run ADK's hallucinations_v1 judge over the same answers."""
    metric = EvalMetric(
        metric_name="hallucinations_v1",
        threshold=0.5,
        criterion=HallucinationsCriterion(
            threshold=0.5,
            judge_model_options=JudgeModelOptions(judge_model=judge_model, num_samples=samples),
        ),
    )
    evaluator = HallucinationsV1Evaluator(metric)
    scores: list[float | None] = []
    for row in rows:
        try:
            result = await evaluator.evaluate_invocations([to_invocation(row)])
            per = result.per_invocation_results or []
            score = per[0].score if per else None
        except Exception as exc:  # judge failures are data, not crashes
            row["judge_error"] = f"{type(exc).__name__}: {exc}"
            score = None
        scores.append(score)
        print(f"  {row['id']:>3}  judge={score}", flush=True)
    return scores


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=agent.DEFAULT_MODEL, help="agent model")
    ap.add_argument("--judge-model", default=agent.DEFAULT_MODEL, help="judge model")
    ap.add_argument("--repeats", type=int, default=1, help="judge passes over identical answers")
    ap.add_argument("--samples", type=int, default=5, help="ADK num_samples per judgement")
    ap.add_argument("--k", type=int, default=agent.TOP_K)
    ap.add_argument("--json", type=Path, help="write the full record here")
    args = ap.parse_args()

    cases = load_cases()
    print(f"Agent: {args.model}   Judge: {args.judge_model}   cases: {len(cases)}\n")

    print("— running the agent —")
    rows = score_deterministic(await run_agent(cases, args.model, args.k), args.k)

    print("\n— judging the same answers —")
    judge_runs: list[list[float | None]] = []
    for r in range(args.repeats):
        if args.repeats > 1:
            print(f"  pass {r + 1}/{args.repeats}")
        judge_runs.append(await score_judge(rows, args.judge_model, args.samples))

    for i, row in enumerate(rows):
        row["judge_scores"] = [run[i] for run in judge_runs]

    answerable = [r for r in rows if r["kind"] == "answerable"]
    summary = {
        "agent_model": args.model,
        "judge_model": args.judge_model,
        "num_samples": args.samples,
        "repeats": args.repeats,
        "recall_at_k": round(mean([r["recall_at_k"] for r in answerable]), 3),
        "mrr": round(mean([r["reciprocal_rank"] for r in answerable]), 3),
        "answers_with_citations": sum(1 for r in rows if r["cited"]),
        "invented_citations": sum(1 for r in rows if r["invented_citations"]),
        "deterministic_failures": [r["id"] for r in rows if r["deterministic_verdict"] == "fail"],
        "needs_human_review": [r["id"] for r in rows if r["deterministic_verdict"] == "review"],
        "judge_no_verdict": sum(1 for r in rows if r["judge_scores"][0] is None),
    }
    print("\n— scorecard —")
    for key, value in summary.items():
        print(f"  {key:>24}: {value}")

    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
