"""A grounded question-answering agent built with Google's Agent Development Kit.

The agent gets exactly one tool — `search_docs` — over the same corpus the labelled
evaluation set in `ai-service/eval/` was written against. It is deliberately the same
task the rest of this repository already solves through a REST boundary, because the
point is not a new capability. It is a controlled comparison: same corpus, same
questions, same labels, different framework.

The system prompt carries the same promise as the FastAPI service: answer only from
the retrieved context, cite the chunk ids, and say so when the answer is not there.
That promise is what the evaluation measures — and `ai-service/eval/metrics.py`
documents one place where its own measurement of that promise is known to be wrong
(case u5). Whether ADK's judge-based metrics close that gap is what `evaluate.py` asks.
"""

from __future__ import annotations

import os

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from index import Chunk, load_chunks, retrieve

OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "ollama_chat/llama3.2:1b")
TOP_K = int(os.environ.get("TOP_K", "4"))

INSTRUCTION = """You answer questions about a software project.

Rules, in order of precedence:
1. Call search_docs exactly once with the user's question before answering.
2. Answer ONLY from the text search_docs returns. Do not use outside knowledge.
3. Cite every claim with the chunk id in square brackets, like [boundary#0].
4. If the returned text does not contain the answer, reply exactly:
   I do not know.
   Do not add a citation to that sentence and do not speculate.

Keep answers to three sentences or fewer."""

# Retrieval results for the current turn, recorded so the evaluation can score what the
# agent actually saw rather than re-running the retriever and hoping it matches.
#
# BUG FOUND BY THE FIRST RUN (2026-09-05). This list used to be cleared inside
# search_docs, which is only correct while the model actually calls the tool. Case a5
# emitted the tool call as literal text instead of calling it — so no clear happened,
# and a4's retrieval was still sitting here when a5's citations were scored. The
# citation check for that row was measuring the previous question. Callers must now
# reset explicitly before each turn; leaving stale state reachable was the defect.
_last_retrieved: list[str] = []


def reset_retrieval() -> None:
    """Clear the recorded retrieval. Call once per question, before asking."""
    _last_retrieved.clear()


def search_docs(question: str) -> dict:
    """Search the project documentation and return the most relevant passages.

    Args:
        question: The user's question, in natural language.

    Returns:
        A dict with a `passages` list; each entry has `chunk_id` and `text`.
    """
    hits: list[Chunk] = retrieve(question, TOP_K)
    _last_retrieved.clear()
    _last_retrieved.extend(c.chunk_id for c in hits)
    return {"passages": [{"chunk_id": c.chunk_id, "text": c.text} for c in hits]}


def last_retrieved() -> list[str]:
    """Chunk ids returned by the most recent search_docs call."""
    return list(_last_retrieved)


def build_agent(model: str = DEFAULT_MODEL) -> LlmAgent:
    """Construct the agent against a LiteLLM-routed model (default: local Ollama)."""
    return LlmAgent(
        name="grounded_qa",
        model=LiteLlm(model=model, api_base=OLLAMA_BASE),
        instruction=INSTRUCTION,
        tools=[search_docs],
    )
