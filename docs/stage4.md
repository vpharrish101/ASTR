# Stage 4: Measure what the checker adds

I evaluated all 32 bundles using three configurations. I used 24 during development and kept eight reserved until the implementation was frozen. The evaluation runner checks the hashes of the implementation and dataset before running the reserved cases. The labels are read by the scorer and stay outside the reviewer input.

| Configuration | What it checks |
|---|---|
| **Sampled checkpoint** | Runs a default ticket and one-field and two-field variations through the effective policy and declared workflow. It reports a blocker if it finds a difference. Otherwise it leaves the review incomplete. |
| **Final reviewer** | Runs the complete deterministic review, including whole-policy route comparisons and the local workflow checks. |
| **Ablation** | Disables whole-policy route comparison and keeps the reachable dead-end and linked-predicate checks. Skipped comparisons remain unresolved. |

All three configurations made **zero model calls**. The four scripted model-failure bundles have valid routing. The offline scorer therefore expects a routing pass and preserves each original model-failure label alongside that expectation.

## How I scored the output

I checked the verdict against the expected answer and counted false passes, false blocks, and incomplete results. For a defect-location match, a supported blocker had to cite the expected rule and artifact, version, and location. For a useful counterexample, the expected route had to agree with the independent reference oracle, and replay had to reproduce the reported actual route.

| Configuration | Correct verdicts | Defects found | Expected locations cited | `INCOMPLETE` |
|---|---:|---:|---:|---:|
| Sampled checkpoint | 13/32 | 9/16 | 8/16 | 23 |
| Final reviewer | **32/32** | **16/16** | **16/16** | **4** |
| Ablation | 8/32 | 4/16 | 4/16 | 28 |

## What the ablation showed

[`d10_urgency_before_missing`](../data/development/d10_urgency_before_missing.json) puts urgency ahead of the missing-category check. Both conditions are individually plausible. Together they send an urgent ticket with no category to `priority_queue` when it should go to `manual_review`. The full reviewer caught that difference. With whole-policy comparison disabled, the ablation left the case `INCOMPLETE`.

The final reviewer matched **all eight reserved verdicts** and produced **22 useful blocker witnesses** across the full set. I saved the live Ollama run separately. Its 24 development verdicts matched the live-mode expectations, and 20 of the 21 bundles that called Ollama produced an accepted model response.

## Where to check the results

```sh
python -m evaluation.run --verify --output reports/stage4-offline.json
```

The command runs **80 offline tests**, prints the metrics, and saves the per-bundle reports to `reports/stage4-offline.json`. The submitted offline results are in [`evaluation/results/offline.json`](../evaluation/results/offline.json), and the separate live results are in [`evaluation/results/live-development.json`](../evaluation/results/live-development.json). The [engineering note](engineering-note.pdf) includes the two debugging examples and their fixes.
