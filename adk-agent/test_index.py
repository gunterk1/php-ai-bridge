"""Tests for the retriever, and one guard that protects the labels.

`ai-service/eval/dataset.yaml` keys its expected_sources to a specific chunk split. If
the chunker drifts, those labels silently point at text they no longer name and every
retrieval number downstream becomes fiction — with no error anywhere. That is exactly
the failure the dataset's own comment warns about ("If the splitter configuration
changes, regenerate the labels rather than loosening the metric"). So the split is a
test, not a comment.
"""

from __future__ import annotations

import index


def test_split_matches_the_labelled_dataset():
    assert index.actual_split() == index.EXPECTED_SPLIT


def test_chunk_ids_use_the_label_format():
    for chunk in index.load_chunks():
        stem, _, n = chunk.chunk_id.partition("#")
        assert stem and n.isdigit(), chunk.chunk_id


def test_retrieval_is_deterministic():
    q = "How do I switch the service from OpenAI to a local model?"
    assert [c.chunk_id for c in index.retrieve(q, 3)] == [c.chunk_id for c in index.retrieve(q, 3)]


def test_retrieval_respects_k():
    assert len(index.retrieve("anything", 2)) == 2
    # k larger than the corpus returns the whole corpus rather than padding or failing.
    assert len(index.retrieve("anything", 99)) == len(index.load_chunks())


def test_ties_break_on_chunk_id_not_file_order():
    # A question sharing no vocabulary with the corpus scores every chunk zero. The
    # order must still be stable, and it must be the id order.
    hits = [c.chunk_id for c in index.retrieve("zzzz qqqq", 5)]
    assert hits == sorted(hits)
