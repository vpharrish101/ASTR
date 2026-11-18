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
