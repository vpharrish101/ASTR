# ASTR

ASTR prepares versioned inputs for a ticket-routing workflow reviewer. Stage 1 parses anchored requirements, resolves scoped approvals, validates artifact references, and inspects linked Python without execution.

## Run Stage 1

Use Python 3.12. Install the pinned dependencies once, then run the CLI and tests locally on CPU.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m src.main data/development/d01_reference.json
.venv/bin/python -m pytest -q
```

The CLI returns JSON and exits with 0 for `READY`, 1 for `UNRESOLVED`, or 2 for invalid or unreadable input. `READY` means Stage 1 found no preparation issue. A syntactically valid wrong threshold, omitted rule, or wrong branch can be ready for semantic review. The final verdict belongs to later stages.

The [input schema](config/bundle.schema.json) and [preparation schema](config/preparation.schema.json) are exported by `python -m src.main --schema bundle` and `python -m src.main --schema preparation`. See the [contract and reference policy](docs/stage1.md) for semantics and evidence conventions.

The CLI lives in `src/main.py`, preparation logic in `src/stage1/`, and shared contracts in `src/utils/`. [decisions.md](decisions.md) records design choices, verification, and plans for later stages.

## Dataset

The dataset contains 24 development bundles and 8 reserved bundles. The hand-authored reference lives in `data/development/d01_reference.json`. Labels and hand-worked routes live in `data/expected/`, outside the reviewer. The [freeze manifest](evaluation/freeze.json) records the inputs, labels, generation script, and fake-client scenarios by hash. Stage 1 tests prepare only development bundles. The reserved split is for later frozen scoring.

Run `python -m evaluation.build_cases` from the project root to reproduce the frozen input files from the reference and named edits. Keep the committed freeze manifest as the comparison point when checking for changes. The verification command above checks manifest integrity, the reference oracle, and Stage 1 behavior. The model scenarios are pinned inputs for Stage 3, and the evaluator will score the frozen labels after the behavioral reviewer is implemented.

## Run Stage 2

```sh
.venv/bin/python -m src.main --review data/development/d01_reference.json
.venv/bin/python -m src.main --review data/development/d09_exclusive_boundary.json
.venv/bin/python -m evaluation.checkpoint data/development/d01_reference.json
```

`--review` runs the deterministic semantic checker and emits a [versioned evidence report](config/review.schema.json). Exit 0 means `PASS`; exit 1 means `CHANGES_REQUIRED` or `INCOMPLETE`. A null actual route indicates a reachable dead end in a routing mismatch. A linked predicate finding can also show distinct branch choices when the final destinations happen to agree.

Z3 compares first-match routing over the declared domain. A counterexample is replayed with the original evidence. Equivalence is scoped to the supported DSL, structured conditions, workflow, and linked Python subset. The checkpoint samples a fixed set of tickets. See [decisions.md](decisions.md) for the method and verification limits.

## Run Stage 3

```sh
.venv/bin/python -m src.main --review data/development/d01_reference.json
.venv/bin/python -m src.main --review --fake-script evaluation/model_scenarios.json data/development/d22_model_timeout.json
```

The first command uses offline deterministic review. The second requires model review and uses the local scripted fake. Cases absent from the script receive a valid synthetic response. The [assisted review schema](config/assisted-review.schema.json) describes both modes and can be exported with `--schema assisted-review`. The base Stage 2 report schema remains available with `--schema review`.

The [reviewer prompt](config/reviewer_prompt.md) asks for one row per effective rule. The application checks the current request ID, rule set, evidence ID, exact quote, and route against freshly prepared inputs. The report keeps model coverage and human follow-ups in `model_review`. Stage 2 findings and deterministic coverage remain in their own fields. `code_review` distinguishes syntax parsing, supported-subset inspection, and execution.

Review inputs are limited to 1 MiB. A model request, including its prompt, is limited to 64 KiB and a response to 16 KiB. The model gets at most two attempts within a shared 10-second budget. The client interface requires the transport to enforce its timeout and response cap. Reports retain request and prompt hashes, limits, attempt outcomes, response sizes, and response hashes for bounded bodies. A failed required model review yields `INCOMPLETE` when there is no evidenced blocker. An evidenced blocker retains `CHANGES_REQUIRED`.

The fake-client tests measure integration, evidence validation, and failure handling. Run the local Ollama client separately to measure live-model results.

### Run a real local model

Start with **Qwen3 4B**. Qwen3 supports non-thinking mode, and Ollama accepts the existing response schema directly. Use Qwen3 8B as a comparison on the same development inputs if you want to measure the effect of model size. [Qwen3](https://github.com/QwenLM/Qwen3) · [Ollama API](https://github.com/ollama/ollama/blob/v0.40.16/docs/api.md)

Install [Ollama v0.40.16](https://github.com/ollama/ollama/releases/tag/v0.40.16) or a compatible later release, and curl 8.4 or newer. Start the server in one terminal.

```sh
OLLAMA_NO_CLOUD=1 ollama serve
```

In a second terminal, from the repository root, use the Python environment from Stage 1.

```sh
ollama pull qwen3:4b
.venv/bin/python -m src.main --review --ollama-model qwen3:4b data/development/d01_reference.json > report-qwen3-4b.json
```

For 8B, use `qwen3:8b` in both commands. Record `ollama --version` and the model ID shown by `ollama list` with measured results. Keep those results separate from the fake-client evaluation.

The client connects to `127.0.0.1:11434`, uses CPU, and disables thinking, input truncation, and context shifting. It requests an 8,192-token context and up to 2,048 output tokens. Both attempts share a 240-second budget, including cold model loading. The same evidence validator checks the resulting JSON. A timeout, truncated generation, or rejected response leaves the required model check unresolved and preserves any deterministic blocker.
