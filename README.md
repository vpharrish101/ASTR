# ASTR

ASTR checks whether a support-ticket workflow follows its written policy. The
input is one JSON bundle with the policy, spec, workflow graph, linked Python,
and approval records. If two parts route the same ticket differently, the
report gives you that ticket and the rule or branch to check.

## Run it

From the repo root, set up Python 3.12 and install the pinned dependencies.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Try the boundary case first. It runs on CPU without a model.

```sh
python -m src.main --review data/development/d09_exclusive_boundary.json
```

Look under `findings` in the JSON. Rule R5 says to escalate at 30 minutes or
more, but the spec says more than 30. A ticket waiting exactly 30 minutes does
the job. The policy sends it to escalation and the spec sends it to the normal
queue. The report cites the source line and the spec condition.

| Status | Meaning |
|---|---|
| `PASS` | The required checks completed and found no blocker. |
| `CHANGES_REQUIRED` | The reviewer has evidence of a blocker. |
| `INCOMPLETE` | A required check is unresolved and no blocker has been established. |

Exit 0 means `PASS`. Exit 1 covers both other review verdicts. Exit 2 means the
input was invalid or unreadable.

## Prerequisites: Rules and fixtures

I wrote a small language for ticket rules. Each line has an ID, a condition,
and a destination.

```text
R5: when waiting >= 30 min -> escalation
```

The first matching rule wins, and `otherwise` gives the fallback. You can
check for missing values, compare numbers or categories, use seconds or
minutes, and group conditions with `and`, `or`, and parentheses. Rule order
matters. A ticket can be urgent and missing a category at the same time.

I started with one hand-written policy and a routing table, then made 32
synthetic bundles with changes such as an approved threshold, an equivalent
condition, or a missing branch. I used 24 during development and reserved
eight until the implementation was frozen. The answer key lives in
[`data/expected/`](data/expected/).

## Stage 1: Resolve the policy

Pydantic validates the JSON against the bundle schema. In `policy.py`, I wrote
a Lark grammar and a `RuleTransformer` that turns each rule line into a `Rule`
with a condition and a route. `resolve_policy()` checks those against the
declared ticket fields and routes and saves the original line and column.

For approvals, `resolve_policy()` keeps records marked `approved` that target
the current revision. It follows their `supersedes` links to select one
replacement per rule. A change from 30 to 25 minutes keeps the rule in the same
position and records both the original line and the approval. Competing
approvals with no clear winner leave that rule unresolved.

In `workflow.py`, `inspect_artifacts()` checks rule references, the workflow
entry and cycles, and the exact snippet ID and version. For a linked Python
snippet, I used `ast.parse()` and translated the supported return expression in
`decide(ticket)` into the same condition objects used by the DSL. The code
records syntax errors and unsupported expressions separately. It never
executes the snippet.
[Stage 1 contract](docs/stage1.md) · [Bundle schema](config/bundle.schema.json)

**Output:** The effective policy, parsed Python predicates, and any unresolved issues. The source is in `src/stage1/`.

### Run Stage 1 on all 24 development bundles

```sh
mkdir -p reports/stage-1
for bundle in data/development/*.json; do
  python -m src.main "$bundle" \
    > "reports/stage-1/$(basename "$bundle")" || [ "$?" -eq 1 ] || break
done
```

Expect 21 `READY` and 3 `UNRESOLVED` reports in `reports/stage-1/`.

## Stage 2: Give all four versions the same ticket

[Stage 2 routing checks](docs/stage2.md)

I built four versions of the routing logic: the effective policy, the spec, the workflow graph, and the workflow with its linked Python predicates. **Z3** checks whether a valid ticket can get different routes under those versions.

The 30-minute case is my favorite. The policy says `>= 30 min`, the spec says `> 30 min`, and a ticket waiting **exactly 30 minutes** settles it. ASTR replays the ticket and reports **expected escalation, actual normal queue**. It cites the rule and spec condition. The same approach catches precedence bugs where each condition looks reasonable on its own.

**Output:** Findings with replayable tickets, evidence locations, and a record of which checks completed. The checker and replay logic are in `src/stage2/`.

If Z3 proves no ticket differs, the comparison is equivalent within the
supported domain. A solver limit or missing evidence leaves the affected check
unresolved. The [review schema](config/assisted-review.schema.json) describes the
findings, coverage, and check results.

### Run Stage 2 on all 24 development bundles

```sh
mkdir -p reports/stage-2
for bundle in data/development/*.json; do
  python -m src.main --review "$bundle" \
    > "reports/stage-2/$(basename "$bundle")" || [ "$?" -eq 1 ] || break
done
```

Expect 9 `PASS`, 12 `CHANGES_REQUIRED`, and 3 `INCOMPLETE` in `reports/stage-2/`.

## Stage 3: Give the evidence a second reader

[Stage 3 model review](docs/stage3.md)

Ollama runs Qwen3 locally. When I enable model review, ASTR sends the effective
rules and findings after the deterministic checks. Qwen can suggest a human
follow-up, such as clarifying which exception takes priority. The app validates
the request ID, rule coverage, evidence IDs, exact quotes, and route claims.
It records model coverage separately and keeps any proven routing blocker.

### Run Stage 3 with the fake client

The scripted fake exercises model failures across the 24 development bundles.

```sh
mkdir -p reports/stage-3-fake
for bundle in data/development/*.json; do
  python -m src.main --review --fake-script evaluation/model_scenarios.json \
    "$bundle" > "reports/stage-3-fake/$(basename "$bundle")" || [ "$?" -eq 1 ] || break
done
```

Expect 6 `PASS`, 12 `CHANGES_REQUIRED`, and 6 `INCOMPLETE` in `reports/stage-3-fake/`.

### Run Stage 3 with Ollama

For a real run, use Ollama v0.40.16 or a compatible later release and curl
8.4+. Start `OLLAMA_NO_CLOUD=1 ollama serve` in one terminal, then run these
commands from the repo root in another.

```sh
ollama pull qwen3:4b
mkdir -p reports/stage-3-ollama
for bundle in data/development/*.json; do
  python -m src.main --review --ollama-model qwen3:4b \
    "$bundle" > "reports/stage-3-ollama/$(basename "$bundle")" || [ "$?" -eq 1 ] || break
done
```

Check `model_review.result` in each of the 24 reports in `reports/stage-3-ollama/`.

On my 24 development bundles, the verdicts matched and the model response was
accepted on 20 of the 21 bundles that called Ollama. Both attempts on the
ignored-approval case failed exact-quote validation. The app kept the
deterministic blocker. The run made 22 calls, including one retry, with a median
of 6.54 seconds per call. GPU offload and the Ollama version were unverified.
[Live reports](evaluation/results/live-development.json)

## Stage 4: Run the frozen evaluation

[Stage 4 evaluation](docs/stage4.md)

```sh
python -m evaluation.run --verify --output reports/stage4-offline.json
```

This runs 80 offline tests and compares three configurations on the same 32
bundles with zero model calls. The checkpoint tries sample tickets. The ablation
skips whole-policy route comparison and keeps the reachable dead-end and linked
predicate checks. Skipped comparisons stay unresolved.

| Configuration | Verdicts matched | Defects found | Expected defect locations cited |
|---|---:|---:|---:|
| Sampled checkpoint | 13/32 | 9/16 | 8/16 |
| Final reviewer | **32/32** | **16/16** | **16/16** |
| Ablation | 8/32 | 4/16 | 4/16 |

The final reviewer matched all eight reserved verdicts and produced 22 replayable
blocker witnesses. The four scripted model-failure cases keep their original
labels in the file. The offline comparison scores their deterministic routing.

## End: What to check

- **One bundle:** The boundary example above prints a JSON report. Look for
  `CHANGES_REQUIRED` and the 30-minute ticket under `findings`.
- **All 32 bundles:** The Stage 4 command writes the per-bundle reports and
  metrics to `reports/stage4-offline.json`. [Saved offline run](evaluation/results/offline.json)
- **Real model results:** The Ollama reports cover the 24 development bundles
  and are saved separately. [Live reports](evaluation/results/live-development.json)

The [engineering note](docs/engineering-note.pdf) explains the supported scope
and the two bugs I fixed during development.
