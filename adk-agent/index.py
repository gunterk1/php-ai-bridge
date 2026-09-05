"""Deterministic chunker and retriever over the shared evaluation corpus.

Retrieval is deliberately dumb: fixed-size chunking and lexical overlap scoring, no
embeddings, no vector store. That is not a shortcut — it is the control. The question
this directory asks is about the *judge*, and a stochastic retriever would put a second
source of variance between the agent and the measurement. With this retriever the same
question returns the same chunks on every run, so any variance observed downstream
belongs to the model or to the judge.

Chunking mirrors `ai-service/eval/dataset.yaml`, which documents the expected split at
chunk_size 800: boundary 1, contracts 1, retry 1, providers 2. `verify_split()` checks
that at import time in the tests, because the labels in that file are keyed to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CORPUS = Path(__file__).resolve().parent.parent / "ai-service" / "eval" / "corpus"
CHUNK_SIZE = 800

# The split the labels were verified against. A mismatch means the labels no longer
# point at the text they name, and every retrieval metric downstream becomes fiction.
EXPECTED_SPLIT = {"boundary": 1, "contracts": 1, "providers": 2, "retry": 1}

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be by do does for from how i if in is it its of on or "
    "that the this to what when where which who why with you your".split()
)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str


def _split(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Split on paragraph boundaries, packing paragraphs up to `size` characters.

    Paragraph-aligned rather than character-aligned: cutting mid-sentence produces
    chunks that read as broken to a model and skew grounding judgements for reasons
    that have nothing to do with retrieval.
    """
    parts: list[str] = []
    current = ""
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        if current and len(current) + len(para) + 2 > size:
            parts.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        parts.append(current)
    return parts or [""]


def load_chunks(corpus: Path = CORPUS) -> list[Chunk]:
    """Chunk every corpus document under `<stem>#<n>`, matching the label format."""
    chunks: list[Chunk] = []
    for path in sorted(corpus.glob("*.md")):
        for i, part in enumerate(_split(path.read_text(encoding="utf-8"))):
            chunks.append(Chunk(f"{path.stem}#{i}", part))
    return chunks


def actual_split(chunks: list[Chunk] | None = None) -> dict[str, int]:
    """Chunks per document, for comparison against EXPECTED_SPLIT."""
    counts: dict[str, int] = {}
    for c in chunks if chunks is not None else load_chunks():
        counts[c.chunk_id.split("#", 1)[0]] = counts.get(c.chunk_id.split("#", 1)[0], 0) + 1
    return counts


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def retrieve(question: str, k: int, chunks: list[Chunk] | None = None) -> list[Chunk]:
    """Return the k chunks with the highest term overlap, ties broken by chunk_id.

    Ties are broken deterministically rather than by list order so that a corpus file
    being renamed cannot silently reorder results.
    """
    pool = chunks if chunks is not None else load_chunks()
    q = _terms(question)
    scored = sorted(
        pool,
        key=lambda c: (-len(q & _terms(c.text)), c.chunk_id),
    )
    return scored[:k]
