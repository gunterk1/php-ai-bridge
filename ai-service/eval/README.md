# Evaluating the RAG pipeline

The system prompt in `app/rag.py` makes two promises:

> You answer strictly from the provided context. **If the answer is not in the context, say you do not know.** Reference the source ids (shown in square brackets) that you used.

Both were untested. A README can claim a pipeline is grounded; only a labelled dataset
can show it. This directory is that dataset plus the harness that runs it.

## What is measured

| Metric | Needs a model to judge? | What it answers |
|---|---|---|
| **recall@k** | no | Did retrieval return the chunk that actually contains the answer? |
| **MRR** | no | …and how early in the list? A chunk found in position four still scores 1.0 recall@4 while three quarters of the context window went to noise. |
| **citation grounding** | no | Does every `[id]` in the answer refer to a chunk the retriever actually returned? |
| **abstention rate** | heuristic | On questions the corpus cannot answer, did the model say so? |

Three of the four are deterministic set arithmetic. That is deliberate: the parts you
must be able to trust are the parts that do not depend on a second model's opinion.
Abstention is the exception and is treated as a screen, not a verdict — see
*Known limitations*.

## Layout

```
eval/
  corpus/         four short documents on distinguishable topics
  dataset.yaml    8 answerable questions with labelled source chunks, 5 unanswerable
  metrics.py      pure functions — no network, no model, no config
  test_metrics.py 31 unit tests for those functions
  run_eval.py     ingests the corpus over REST, queries, scores, prints a scorecard
```

The runner talks to a running service over the same three endpoints the PHP and
TypeScript applications use. It measures the system as deployed — retriever settings,
provider, model — rather than reaching past the boundary to test something users never
touch.

## Running it

```bash
# unit tests: no service, no keys, no network
cd ai-service && python -m unittest discover -s . -p "test_*.py"

# full evaluation against a running service
python -m eval.run_eval                       # http://localhost:8000, k=4
python -m eval.run_eval --k 2 --json out.json
```

Exit code is 1 when a hard invariant breaks — an invented citation, or an unanswerable
question that got answered. Retrieval quality is reported but never fails the run: it is
a number to watch over time, not a gate.

## Results

Measured against a **self-hosted** setup: Ollama, `llama3.2:1b` for chat,
`nomic-embed-text` for embeddings. A 1B model is the hard case on purpose — it is what
runs on a laptop, and privacy-first deployments are exactly where small local models get
used.

| | k=2 | k=4 |
|---|---|---|
| recall@k | 1.000 | 1.000 |
| MRR | 0.938 | 0.938 |
| abstention rate | **0.400** | **1.000** |
| invented citations | **3** (a1, u3, u4) | 0 |
| answered when unanswerable | **3** (u2, u3, u5) | 0 |

**The failure mode is context starvation, not context overload.** With four chunks the
model refuses every unanswerable question and cites cleanly. With two it invents. The
most alarming single output, at k=2:

> According to the context, the Kubernetes operator for this project is maintained by **[OpenAI]**.

There is no Kubernetes operator and no such source id. Confident, sourced-looking,
entirely fabricated — and caught by a set-membership check that costs nothing to run.
Two further cases (`a1`, `u4`) abstained correctly but cited a placeholder,
`I do not know. [context]`: harmless in substance, still a broken contract.

## What the suite found in the code

**A non-idempotent ingest.** Running the evaluation twice against the same live service
produced `['providers#1', 'providers#1']` — the same chunk in both top-k slots.
`/ingest` appended a second copy of every document instead of replacing it, so half the
context window went to a duplicate and genuinely different chunks were crowded out.
Reproduced in isolation, then fixed: `RagEngine.ingest` now tracks chunk ids per
`doc_id` and deletes the previous set before adding. Ingesting the same document three
times now yields one chunk and zero duplicates.

The fix moved a metric. Before it, recall@2 was 0.750 with `contracts#0` never retrieved
for question a7 — the duplicates were occupying the slot. After it, recall@2 is 1.000.
That is the whole argument for measuring: the bug was invisible in normal use and showed
up as a number.

**A labelling error of my own.** The first run reported a retrieval miss on a4. It was
not a miss — `providers.md` is 866 characters and splits into two chunks at
`chunk_size=800`, so the answer lives in `providers#1` while my label said
`providers#0`. Verified against the actual splitter output and corrected. Worth stating
plainly: the first thing an eval suite finds is usually a bug in the eval suite.

## Known limitations

**Abstention detection is a word list, and word lists are wrong in both directions.**
The first run scored 0.2 abstention when the true figure was higher — four refusals
phrased as *"there is no mention of a hosted plan"* matched no marker. Those phrasings
were added.

The opposite error cannot be fixed the same way. Case u5, verbatim:

> According to the provided context, for a 4xx response (including 429), the PHP client
> performs retrials with exponential backoff of 200 milliseconds and then 400
> milliseconds. However, any 4xx response is surfaced immediately without a retry.
> Therefore, I do not know how many retries the PHP client performs on a 404.

The first sentence contradicts the source. The second contradicts the first. The third
is a refusal. The screen sees *"I do not know"* and scores a clean abstention — in truth
it is the worst answer in the set. Separating *"there is nothing about X here"* from
*"X is 47, but I do not know"* is a semantic judgement, not a lexical one.

So it is documented and pinned by a test asserting the current, wrong behaviour, rather
than patched with a cleverer regex that would fail somewhere less visible. Anything that
matters gets a second pass by a reader or a judge model.

**Corpus size.** Four documents, five chunks. Large enough to make MRR and the
abstention cases meaningful, too small for recall@4 to discriminate — at k=4 the
retriever returns essentially everything, which is why the k=2 column exists. Growing the
corpus is the obvious next step, and the labels must grow with it.

**Single provider, single model.** These numbers describe `llama3.2:1b` on Ollama. A
larger or hosted model will score differently; the harness is the transferable part, not
the table.
