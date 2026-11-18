# Stage 3: Have the model review the evidence

**Ollama runs Qwen3 locally.** I use it after Stage 2 to give the evidence a second reader. Qwen can suggest a human follow-up, such as clarifying which exception takes priority or confirming the wording of an approval. The application supplies the rules and findings and decides which parts of the reply it can accept.

## What goes into a model request

`assisted_review()` runs the deterministic checker first. Plain `--review` runs offline. Passing `--ollama-model` or `--fake-script` makes model review a required check for that run. If there are preparation issues or unresolved deterministic checks, the report records why the model was skipped. A bundle with a proven blocker can still go to the model when its deterministic checks are complete.

The request includes each effective rule, its original source, any applicable approval excerpt, and the deterministic findings. A request ID binds the response to the bundle, prompt, and settings. Ollama gets the Pydantic response schema as its structured output format. The fake client goes through the same application validator, which lets me reproduce specific failures.

## What counts as an accepted reply

The model must return exactly one row per requested rule. Each row contains the route **when that rule fires**, an evidence ID, the exact quote, and a follow-up choice. ASTR checks that:

- The request ID matches this run, and every requested rule appears exactly once.
- The evidence ID belongs to that rule, and the quote matches the designated text exactly. For an approved replacement, that means quoting the applicable approval.
- The route claim matches the effective rule, and the follow-up is one of the allowed options.

An invalid row rejects the whole response. The application allows at most two attempts within a shared deadline and records each attempt’s result, response size, and elapsed time. Model coverage and follow-ups go under `model_review`. The deterministic findings and coverage stay in their own fields.

## Worked example

In the live run of [`d15_ignored_approval`](../data/development/d15_ignored_approval.json), the approved threshold was **25 minutes** and the implementation still used **30**. Stage 2 established the routing defect. Both model attempts failed exact-quote validation, so `model_review` recorded the failure and the overall result stayed `CHANGES_REQUIRED`. If the same model failure occurred on a bundle with no evidenced blocker, the required check would make the result `INCOMPLETE`.

The [Pydantic contracts](../src/stage3/contracts.py) define the model response, fake script, and review report. The [client adapters](../src/stage3/clients.py) handle Ollama and simulated replies. The [review flow](../src/stage3/model_review.py) builds the request, validates the response, and assembles the report. The prompt is in [`config/reviewer_prompt.md`](../config/reviewer_prompt.md), and the scripted failures are in [`evaluation/model_scenarios.json`](../evaluation/model_scenarios.json).
