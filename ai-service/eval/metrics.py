"""Metrics for retrieval-augmented answering.

Every function here is pure: strings and lists in, numbers and verdicts out. No
network, no model, no configuration. That is deliberate — the metrics are the part
you must be able to trust, so they are the part that is unit-tested. The runner
(`run_eval.py`) does the talking to a live service and feeds the results in here.

Two of the three metrics need no judge at all. Retrieval quality is measured against
labelled expectations, and citation grounding is a set-membership check. Only
abstention involves interpretation, and that one deliberately refuses to pretend it
is certain — see `classify_abstention`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

# Source ids are emitted into the prompt context as "[doc#3] text …" and the model
# is asked to reference them the same way. Anything inside square brackets in the
# answer is therefore a citation candidate.
_CITATION = re.compile(r"\[([^\[\]]{1,120})\]")

# Phrases that signal the model declined to answer. English and German, because the
# same corpus gets queried in both. Kept explicit rather than clever: a short list
# you can read and argue with beats a regex nobody can audit.
_REFUSAL_MARKERS = (
    "i do not know",
    "i don't know",
    "i do not have",
    "not in the context",
    "not contained in the context",
    "no information",
    "cannot answer",
    "can't answer",
    "cannot be answered",
    "unable to answer",
    "does not contain",
    "does not mention",
    "no mention of",
    "is not mentioned",
    "not specified",
    "no documents have been ingested",
    "ich weiß es nicht",
    "ich weiss es nicht",
    "nicht im kontext",
    "keine informationen",
    "kann ich nicht beantworten",
    "geht aus dem kontext nicht hervor",
)

# If the answer is this short and contains no citation, it is almost certainly a
# refusal even when the wording is not on the list above.
_TERSE_ANSWER_CHARS = 80


def recall_at_k(expected: Sequence[str], retrieved: Sequence[str]) -> float:
    """Fraction of the expected sources that appear anywhere in the retrieved set.

    Returns 1.0 when nothing was expected — a question with no required source
    cannot fail retrieval, and scoring it 0.0 would drag the average down for the
    unanswerable cases, which is exactly backwards.
    """
    if not expected:
        return 1.0
    found = sum(1 for src in set(expected) if src in set(retrieved))
    return found / len(set(expected))


def reciprocal_rank(expected: Sequence[str], retrieved: Sequence[str]) -> float:
    """1/rank of the first expected source in the retrieved list, 0.0 if absent.

    Averaged over a dataset this is MRR. It answers a different question than
    recall: not "did we find it" but "did we find it *early*". A retriever that
    always puts the right chunk in position four still scores 1.0 recall@4 while
    quietly spending most of the context window on noise.
    """
    if not expected:
        return 1.0
    wanted = set(expected)
    for position, source in enumerate(retrieved, start=1):
        if source in wanted:
            return 1.0 / position
    return 0.0


@dataclass(frozen=True)
class CitationCheck:
    """Result of comparing the citations in an answer against what was retrieved."""

    cited: tuple[str, ...]
    invented: tuple[str, ...]

    @property
    def grounded(self) -> bool:
        """True when every citation refers to a chunk the retriever actually returned."""
        return not self.invented


def check_citations(answer: str, retrieved: Iterable[str]) -> CitationCheck:
    """Verify that every `[id]` in the answer refers to a retrieved chunk.

    This catches a specific and nasty failure mode: an answer that looks properly
    sourced but points at chunk ids the retriever never returned. The reader has no
    way to tell — the citation format is identical. A set check does.
    """
    available = set(retrieved)
    cited = tuple(dict.fromkeys(_CITATION.findall(answer or "")))
    invented = tuple(c for c in cited if c not in available)
    return CitationCheck(cited=cited, invented=invented)


def classify_abstention(answer: str) -> str:
    """Classify an answer as 'abstained', 'answered' or 'unclear'.

    Three states on purpose. Abstention detection by keyword is a heuristic, and a
    heuristic that returns a boolean invites you to forget that. 'unclear' is the
    honest verdict for anything that neither matches a refusal marker nor looks like
    a substantive answer — those rows belong in front of a human, not in an average.

    KNOWN LIMITATION, measured rather than assumed. A refusal marker anywhere in the
    answer wins, so an answer that asserts something false and *then* backs off is
    scored as a clean abstention. The first run of this suite produced exactly that
    (case u5, llama3.2:1b): the model claimed 4xx responses are retried with backoff,
    contradicted itself in the next sentence, and closed with "Therefore, I do not
    know". Scored 'abstained'; in truth the worst answer in the set.

    That gap is semantic — separating "there is nothing about X here" from "X is 47,
    but I do not know" needs a reader, not a word list. So it is documented and
    tested (see test_metrics.py) instead of papered over with a cleverer regex that
    would fail somewhere else. Treat this metric as a screen: trustworthy for the
    deterministic checks around it, indicative for abstention.
    """
    text = (answer or "").strip()
    if not text:
        return "unclear"

    lowered = text.lower()
    if any(marker in lowered for marker in _REFUSAL_MARKERS):
        return "abstained"

    has_citation = bool(_CITATION.search(text))
    if has_citation or len(text) > _TERSE_ANSWER_CHARS:
        return "answered"
    return "unclear"


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean, 0.0 for an empty sequence."""
    return sum(values) / len(values) if values else 0.0
