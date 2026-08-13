"""Unit tests for the evaluation metrics.

Stdlib unittest, no test dependency — same reasoning as the node-app's use of
node:test. Run with:

    python -m unittest discover -s ai-service -p "test_*.py"
"""

from __future__ import annotations

import unittest

from eval.metrics import (
    check_citations,
    classify_abstention,
    mean,
    recall_at_k,
    reciprocal_rank,
)


class TestRecall(unittest.TestCase):
    def test_finds_all_expected(self):
        self.assertEqual(recall_at_k(["a#0"], ["a#0", "b#0"]), 1.0)

    def test_partial(self):
        self.assertEqual(recall_at_k(["a#0", "b#0"], ["a#0", "c#0"]), 0.5)

    def test_missing(self):
        self.assertEqual(recall_at_k(["a#0"], ["b#0", "c#0"]), 0.0)

    def test_order_does_not_matter(self):
        self.assertEqual(recall_at_k(["a#0"], ["z#0", "a#0"]), 1.0)

    def test_duplicates_in_expected_do_not_inflate(self):
        self.assertEqual(recall_at_k(["a#0", "a#0"], ["a#0"]), 1.0)

    def test_no_expectation_scores_full(self):
        # Unanswerable questions carry no expected source. Scoring them 0.0 would
        # punish the retriever for a question that has no right answer to find.
        self.assertEqual(recall_at_k([], ["a#0"]), 1.0)


class TestReciprocalRank(unittest.TestCase):
    def test_first_position(self):
        self.assertEqual(reciprocal_rank(["a#0"], ["a#0", "b#0"]), 1.0)

    def test_second_position(self):
        self.assertEqual(reciprocal_rank(["a#0"], ["b#0", "a#0"]), 0.5)

    def test_fourth_position(self):
        self.assertEqual(reciprocal_rank(["a#0"], ["b#0", "c#0", "d#0", "a#0"]), 0.25)

    def test_absent(self):
        self.assertEqual(reciprocal_rank(["a#0"], ["b#0"]), 0.0)

    def test_takes_the_earliest_of_several_expected(self):
        self.assertEqual(reciprocal_rank(["a#0", "b#0"], ["c#0", "b#0", "a#0"]), 0.5)

    def test_recall_can_be_perfect_while_rank_is_poor(self):
        # The reason both metrics exist: the chunk was found, but only in position
        # four, so three quarters of the context window went to noise.
        retrieved = ["x#0", "y#0", "z#0", "a#0"]
        self.assertEqual(recall_at_k(["a#0"], retrieved), 1.0)
        self.assertEqual(reciprocal_rank(["a#0"], retrieved), 0.25)


class TestCitations(unittest.TestCase):
    def test_grounded(self):
        r = check_citations("As stated in [a#0], the boundary is REST.", ["a#0", "b#0"])
        self.assertTrue(r.grounded)
        self.assertEqual(r.cited, ("a#0",))
        self.assertEqual(r.invented, ())

    def test_invented_citation_is_caught(self):
        # The dangerous case: the answer looks properly sourced, but the id was
        # never retrieved. Indistinguishable to a reader, obvious to a set check.
        r = check_citations("According to [ghost#7] the answer is 42.", ["a#0"])
        self.assertFalse(r.grounded)
        self.assertEqual(r.invented, ("ghost#7",))

    def test_mixed_grounded_and_invented(self):
        r = check_citations("See [a#0] and [ghost#7].", ["a#0"])
        self.assertFalse(r.grounded)
        self.assertEqual(r.cited, ("a#0", "ghost#7"))
        self.assertEqual(r.invented, ("ghost#7",))

    def test_no_citations_is_grounded_by_vacuity(self):
        r = check_citations("I do not know.", ["a#0"])
        self.assertTrue(r.grounded)
        self.assertEqual(r.cited, ())

    def test_repeated_citation_reported_once(self):
        r = check_citations("[a#0] says so, and [a#0] again.", ["a#0"])
        self.assertEqual(r.cited, ("a#0",))

    def test_empty_answer(self):
        self.assertTrue(check_citations("", ["a#0"]).grounded)


class TestAbstention(unittest.TestCase):
    def test_english_refusal(self):
        self.assertEqual(classify_abstention("I do not know."), "abstained")

    def test_context_phrasing(self):
        self.assertEqual(
            classify_abstention("That information is not in the context provided."),
            "abstained",
        )

    def test_german_refusal(self):
        self.assertEqual(
            classify_abstention("Das geht aus dem Kontext nicht hervor."), "abstained"
        )

    def test_empty_store_message_counts_as_abstention(self):
        # RagEngine.query returns this verbatim when nothing has been ingested.
        self.assertEqual(
            classify_abstention("No documents have been ingested yet."), "abstained"
        )

    def test_substantive_answer(self):
        answer = (
            "The PHP layer calls the Python service over HTTP so that both sides can "
            "deploy and fail independently [boundary#0]."
        )
        self.assertEqual(classify_abstention(answer), "answered")

    def test_short_answer_with_citation_counts_as_answered(self):
        self.assertEqual(classify_abstention("Three [boundary#0]."), "answered")

    def test_short_uncited_answer_is_unclear_not_answered(self):
        # "42" is not obviously a refusal and not obviously a grounded answer. The
        # metric refuses to guess rather than quietly picking a side.
        self.assertEqual(classify_abstention("42"), "unclear")

    def test_empty_is_unclear(self):
        self.assertEqual(classify_abstention(""), "unclear")
        self.assertEqual(classify_abstention("   "), "unclear")

    def test_refusal_marker_wins_over_length(self):
        answer = (
            "I do not know the answer to that, because the retrieved passages discuss "
            "the retry policy and the contract guards but never mention pricing at all."
        )
        self.assertEqual(classify_abstention(answer), "abstained")

    def test_no_mention_phrasing(self):
        # The four unanswerable questions in the first real run all came back in this
        # shape. The original marker list missed every one of them and scored the run
        # at 0.2 abstention when the true figure was higher.
        self.assertEqual(
            classify_abstention(
                "[providers#0] According to the text, there is no mention of a "
                "hosted plan or its associated costs."
            ),
            "abstained",
        )

    def test_known_false_positive_claim_then_refusal(self):
        """Documents a measured blind spot rather than hiding it.

        Verbatim output from the first suite run (case u5, llama3.2:1b). The answer
        states something the source contradicts — 4xx is never retried — walks it
        back, and closes with a refusal. The screen sees "I do not know" and returns
        'abstained'. That is wrong, and no word list fixes it: the difference between
        this and an honest refusal is semantic.

        The test asserts the current behaviour on purpose. If a future change makes
        this return 'answered' or 'unclear', that is an improvement and the test
        should be updated deliberately — not a regression that silently passes.
        """
        answer = (
            "[retry#0] # Retry policy\n\n"
            "According to the provided context, for a 4xx response (including 429), "
            "the PHP client performs retrials with exponential backoff of 200 "
            "milliseconds and then 400 milliseconds. However, any 4xx response is "
            "surfaced immediately without a retry.\n\n"
            "Therefore, I do not know how many retries the PHP client performs on a 404."
        )
        self.assertEqual(classify_abstention(answer), "abstained")  # known false positive


class TestMean(unittest.TestCase):
    def test_values(self):
        self.assertAlmostEqual(mean([1.0, 0.0, 0.5]), 0.5)

    def test_empty(self):
        self.assertEqual(mean([]), 0.0)


if __name__ == "__main__":
    unittest.main()
