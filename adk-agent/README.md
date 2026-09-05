# adk-agent — where does LLM-as-judge stop being a measurement?

A question-answering agent built with [Google's Agent Development Kit](https://google.github.io/adk-docs/),
scored twice over the same answers: once with the deterministic metrics this repository
already ships, once with ADK's built-in `hallucinations_v1` judge.

Same corpus, same labelled questions, same model. The only variable is the scorer.

## Why this exists

`ai-service/eval/metrics.py` documents a limitation of its own abstention screen, measured
rather than assumed:

> A refusal marker anywhere in the answer wins, so an answer that asserts something false
> and *then* backs off is scored as a clean abstention. […] That gap is semantic —
> separating "there is nothing about X here" from "X is 47, but I do not know" needs a
> **reader**, not a word list.

ADK ships that reader. So the honest next question is not whether a judge is nicer than a
regex, but **at what model capability a judge stops being theatre** — because the reader is
itself a language model, and the one available here is small.

## What was run

| | |
|---|---|
| Agent model | `llama3.2:1b` via Ollama (LiteLLM) |
| Judge model | `llama3.2:1b`, ADK default `num_samples: 5` |
| Cases | 13 labelled (8 answerable, 5 unanswerable) from `ai-service/eval/dataset.yaml` |
| Judge passes | 3, over **byte-identical** recorded answers |
| Retriever | deterministic lexical overlap — see below |

Retrieval is deliberately dumb: fixed chunking, term overlap, no embeddings. That is the
control, not a shortcut. The question is about the judge, and a stochastic retriever would
put a second source of variance between the agent and the measurement.

The chunker reproduces the split `dataset.yaml` documents (`boundary 1, contracts 1,
providers 2, retry 1`), so the existing labels stay valid. A test enforces that, because a
drift would silently point the labels at text they no longer name.

## Findings

### 1. The judge produced a verdict for 12 of 39 judgements — 31%

Not an error. Not an exception. The evaluator returned cleanly, ADK sampled five times per
case, and no usable structured verdict came back for the other 69%.

That is the same failure shape recorded in this repo's `steuer-radar` sibling: constrained
generation abandoned silently, HTTP 200, `finish_reason: "stop"`, no warning anywhere. One
framework layer higher, independently reproduced.

### 2. The silence is not deterministic

Across three passes over identical answers, **6 of 13 cases produced a verdict in one pass
and silence in another**, and 6 produced no verdict at any point.

| case | pass 1 | pass 2 | pass 3 | spread |
|---|---|---|---|---|
| a1 | 0.667 | — | 0.333 | 0.333 |
| a2 | **1.000** | 0.500 | **0.250** | **0.750** |
| a4 | — | 0.600 | — | |
| a8 | 0.667 | 0.500 | — | 0.167 |
| u3 | — | — | 0.400 | |
| u4 | — | 0.500 | 0.000 | 0.500 |
| u5 | **1.000** | — | — | |

Case `a2` scored 1.000, 0.500 and 0.250 on the same string. For an auditable metric that is
disqualifying on its own: a number that changes when nothing changed cannot be evidence.

This also reframes ADK's `num_samples: 5` default. It is not a tuning knob — it is a
built-in countermeasure against exactly this variance, and here it does not contain it.

### 3. The one unambiguous hallucination scored 1.000

`u5` asks something the corpus does not answer. The agent replied:

> The PHP client performs two retries for a 404 status code. [retry#0] and [boundary#0].

A fabricated number, on an unanswerable question, with two real-looking citations. The
deterministic check fails it. The hallucination judge, in the one pass where it spoke, scored
it **1.000 — perfectly clean**.

### 4. Two of three invented citations name chunks that do not exist

`a6` cited `boundary#2` and `u3` cited `boundary#1`. The `boundary` document produces exactly
one chunk. The model invented the source *and* a plausible identifier for it — indistinguishable
from a real citation by eye, caught by a set membership test in one line.

### 5. The run found two defects in this directory's own code

Kept here rather than quietly fixed, because they are the same class of bug the experiment
is about — state that looks right and is not.

- **`agent.py`** cleared its recorded retrieval *inside* the tool. When the model emitted the
  tool call as literal text instead of calling it, the previous question's retrieval was still
  in place, and that row's citation check scored the wrong question. Reset is now explicit and
  per-turn. Fixing it moved `recall@k` from an inflated 1.000 to 0.750.
- **`evaluate.py`** collapsed the three abstention states into pass/fail, so an answer too
  terse to classify slipped through as clean. `metrics.py` warns about precisely this
  (*"those rows belong in front of a human, not in an average"*). There is now a third bucket.

## What this does not show

- **Nothing about `gemini-2.5-flash`,** ADK's default judge. A capable judge may well close the
  gap; that experiment needs an API key this machine does not have. The claim here is about the
  floor, not the ceiling.
- **13 cases, 3 passes, one judge model.** Enough to show the variance exists, not enough to
  characterise it.
- **Not a criticism of ADK.** The agent side worked on the first attempt: typed tools, a clean
  runner, LiteLLM routing to a local model. The evaluation framework is doing what it says —
  asking a language model — and the result is a property of that model.

## Running it

```bash
pip install -r requirements.txt
ollama pull llama3.2:1b
python -m pytest test_index.py -q
python evaluate.py --repeats 3 --json run.json
```

`run-llama1b.json` holds the recorded run these findings come from.
