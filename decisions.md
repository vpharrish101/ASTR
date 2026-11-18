# ASTR decision log

Update this log as decisions are made. Record the choice, why it matters, and the verification performed. Keep plans and completed work visibly separate. Capture actual failures and fixes as they happen so the final engineering note can cite them.

## Prerequisites and scope

- Build one small Python CLI for the ShodhAI assessment. Use a reproducible CPU environment, pinned dependencies, and synthetic inputs. The development toolchain is Python 3.12. The existing cloud checkout at `/workspace/ASTR` started with only a README.
- Treat a human-readable DSL as the written requirements. Author the source policies directly in it. Lark handles the grammar and parser positions, and Pydantic handles the input and output contracts. Both keep the scope specific enough for deterministic verification.
- Use explicit first-match order and one final fallback. In the reference policy, missing category goes to manual review, then fraud goes to manual review, then urgency takes priority, then the enterprise/category exception applies, then the waiting threshold applies. The fallback is the normal queue. Declare field types and permitted values in each bundle. Store ticket durations as integer seconds and support explicit minutes and seconds in the artifacts.
- Author a complete reference bundle by hand before implementing the parser. It contains requirements, a structured spec, a workflow graph, a linked Python predicate, and scoped approval records. A hand-checked routing table covers overlaps, missing category, both enterprise categories, and the 25- and 30-minute boundaries. A separately written routing oracle checks the expected outcomes. See [the contract and reference policy](docs/stage1.md) and [the routing table](data/expected/reference_routes.json).

## Dataset design and freeze

- Double the original 16-bundle proposal to 32 to exercise more variations. Development has 6 valid, 12 defective, and 6 incomplete cases. The reserved split has 2 valid, 4 defective, and 2 incomplete cases.
- Generate variants by applying named edits to the reference bundle. The generator is deterministic and needs no random seed. Cases include equivalent units and conditions, harmless renaming and reordering, omitted rules, scope and supersession of approvals, precedence, branches, code disagreements, injected claims, and model failures.
- Keep expected labels, evidence locations, and sample tickets in `data/expected/`. Keep fake-model scenarios in the evaluation harness. The reviewer takes a bundle path. The generator, inputs, labels, and scenarios are hashed in [the freeze manifest](evaluation/freeze.json). Stage 1 ran on development bundles. Reserved inputs were authored for the project and checked by file hash, with reviewer runs reserved for evaluation.
- Verified that regeneration in a separate directory reproduced the frozen files byte for byte.

## Stage 1

- Use strict Pydantic models for versioned requirements, spec rules, workflow decisions and terminals, linked snippets, and approvals. Keep artifact versions and local locations. A mapped spec rule or workflow node refers to stable source rule IDs. An omission can therefore cite an original source anchor even when the spec has no matching entry.
- Apply approved replacements only to the stated revision and rule. Explicit supersession resolves order, and competing approvals without a unique winner leave the rule unresolved. Preserve both the original source anchor and the winning approval anchor. Approval claims in source text and comments have no authority.
- Use Python's `ast` to parse the supported predicate subset without execution. Validate exact code links and use `graphlib` for the acyclic graph contract. Preserve missing or dangling edges for Stage 2's reachability checks.
- Output `READY` for a prepared bundle and `UNRESOLVED` when a preparation issue remains. Behavioral correctness is a later verdict. Verified that the reference keeps 30 minutes, the approved revision uses 25 minutes, and conflicting approvals leave the threshold unresolved. The initial implementation passed 44 tests in a fresh environment.

## Pre-Stage 2 layout

- Follow the project's `config/`, `data/`, and `src/` layout. Move the DSL and approval code plus artifact inspection to `src/stage1/`. Put the shared Pydantic contracts in `src/utils/contracts.py`. Keep `src/main.py` as the CLI entry point. Add `src/stage2/` with its first implementation.
- Use the existing libraries and small modules. Keep the workflow as an ID-keyed dictionary and avoid introducing a graph framework for traversal.
- Verified the move with the 44 Stage 1 tests and a before-and-after comparison of JSON output and exit codes for all 24 development bundles plus both schema exports.

## Stage 2 plan

- Save a runnable sampled checkpoint before adding exact equivalence checks. Record the tickets used and the limits of sampling.
- Reuse the Stage 1 condition and evidence models. Use Z3 to compare the effective policy, spec, workflow, and code-backed workflow on the declared ticket domain. Traverse the actual graph edges, check linked Python predicates on reachable inputs, and replay each behavioral witness for the report.
- Track source coverage separately from route equality. Preserve unresolved scope and test the positive and negative development cases before frozen evaluation. Capture any substantive debugging examples with the original failing output, focused fix, and regression test.

## Stage 1 audit against the assessment PDF

Verified all three Stage 1 bullets on page 1 before starting semantic review.

| PDF requirement | Implementation and verification |
|---|---|
| Versioned inputs and evidence locations | Pydantic's bundle schema covers the source, spec, graph, snippets, and approval log. Nodes and edges inherit the workflow artifact version and use JSON Pointer locations. Source anchors survive approval replacement. Stage 2 uses that source anchor for the omitted exception. |
| Supported conditions and visible gaps | The DSL and contracts cover missing values, equality, inclusive and exclusive numeric comparisons, nested AND/OR, ordered exceptions, and seconds/minutes. The contract documents the restricted AST. Development tests verify source, Python, and link issues remain visible. |
| Effective policy from applicable approved changes | Revision-scoped metadata and an explicit acyclic supersession order determine applicability. CLI and tests cover 30-minute default, approved 25-minute revision, proposed and out-of-scope changes, supersession, and conflicts with cited records. |

The 44 Stage 1 tests passed before the sampled and exact semantic runs. The frozen dataset and schemas for Stage 1 stayed intact.

## Stage 2 implementation and verification

- Preserve the first working sampled reviewer in `evaluation/checkpoint.py`. It varies up to two ticket fields at once, tests known duration boundaries, and compares effective requirements with the declared workflow. It found both pairwise urgency precedence and wrong-edge defects. A sample with no mismatch reports `INCOMPLETE`. The checkpoint source SHA-256 is `67085c408d792995cc2ac73f3dbe6ce899bafb813dce9ebfe32a3ac0942c57a3`. Its shared replay's subsequent metadata correction left all 24 development checkpoint outputs identical.
- The shared replay SHA-256 after that correction is `cbf75a91e8df6e9eba1ddd60cfc30fd24da36d07bf14c667913e8ecbceca66d7`. The development checkpoint output digest is `99ca92a471ac1de446d1e536b68709307b280a67448db76c45e2cc28ebd80ed1`, computed from the filename-ordered report list serialized with `json.dumps(reports, sort_keys=True)`.
- Use Z3 on the declared ticket domain. Nonnegative integer seconds preserve exact numeric boundaries. Finite category encodings include a distinct null value for nullable fields. Compile the ordered policy, structured spec, and graph independently. Compose graph outcomes in topological order and propagate reachability from the entry. Substitute parsed Python predicates for linked decisions in a separate graph comparison. A reachable missing or dangling edge has a no-route outcome. An unreviewed code predicate has an unresolved outcome.
- Ask for a different route in each effective rule's first-match region. Unsatisfiability proves that comparison for the supported domain. A satisfiable witness is replayed through the source and implementation for an evidence trail. Solver limits and preparation issues remain visible. For unresolved source, a separately proved reachable workflow dead end can still be a blocker.
- Check source links separately. Equivalent behavior with a missing link produces an informational finding. A behavioral omission with a missing spec mapping cites the original source anchor and includes a counterexample. Compare code and graph predicates only on reachable inputs and record branch choices on a script disagreement.
- Debugging record. The first development output for `d07_unapproved_threshold` showed `route_mismatch:R6` twice, although the faulty threshold is R5. The first attribution fix used every visited workflow rule as a candidate, including bypassed rules. The next regression run produced `test_development_semantic_review[d17_wrong_exception_route] assert []` and `test_workflow_trace_does_not_blame_a_condition_that_was_bypassed assert False` (2 failed, 29 passed). Attribute an early match using the selected spec rule or the workflow decision feeding the terminal. Preserve the full path as evidence and keep the expected-path rule separately. Both regressions passed after the fix.
- Verified Stage 2 against all 24 development bundles. The semantic cases produced 6 passes, 12 evidenced blockers, and 3 incomplete results. The remaining 3 bundles have valid routing and carry model-failure scenarios for Stage 3. They passed semantic review only. The entire suite passed 75 tests. The eight reserved bundles stayed out of reviewer runs. Final evaluation and ablation remain later-stage work.

## Library-first cleanup after Stage 2

- Reuse Lark token callbacks with the standard `json.loads`, `str`, and `int` converters. Share evidence creation and versioned validation issues across Pydantic artifacts. Give Stage 1 cycle validation and Stage 2 graph compilation the same dependency map for `graphlib`.
- Compile each workflow with one forward traversal. Propagate Z3 reachability from the entry and record the reachable terminal or unresolved outcome in that pass. Keep the concrete replay for evidence and continue using the existing independent routing oracle in evaluation.
- Share CLI serialization and exit-status handling. The production source fell from 974 to 910 physical lines, or from 812 to 750 when excluding blank lines and standalone comments. Stage 2's two files fell from 379 to 357 lines.
- All 75 tests passed. The 24 development bundles kept their preparation and checkpoint outputs, verdicts, coverage, individual check results, and finding attribution. Three exact checks returned alternate valid witnesses. Verified 200 additional old-versus-new Z3 graph comparisons, covering routes and per-node reachability with and without linked code.
- The checkpoint source and development output digest stayed the same. The shared replay SHA-256 after cleanup is `49549f6e0a566dd9bbb52378f81b75cafe61489d86abd82aedd39c3b3c8e1f80`.

## Stage 2 report responsibilities

- Keep comparison orchestration, coverage, finding construction, and verdict generation in the existing Stage 2 module. Record each check once and derive per-rule coverage from those records. Findings attach concrete paths, evidence, and corrections. The verdict function gives evidenced blockers priority over unresolved scope.
- Reuse the declared workflow results when Z3 produces a structurally identical code-backed route expression. Identical normalized predicates also need no solver search. Search for unknown code outcomes only when there is an unresolved link or predicate. The reference bundle used 12 solver searches after the change, down from 25.
- All 75 tests passed. Across the 24 development bundles, verdicts, coverage, individual checks, and finding attribution matched the previous implementation. Two cases returned different valid witnesses. Stage 1 and sampled checkpoint reports matched exactly. The module grew from 273 to 295 physical lines as the reporting responsibilities became explicit. The report schema and frozen inputs stayed intact.

## Proof reuse boundary

- Reproduced a stale result through the direct Python API. After preparing the reference at v1, changing only the requirements revision to v2 and supplying the old preparation returned `PASS`. Preparing v2 fresh returned `CHANGES_REQUIRED` because the 25-minute approval became applicable. The CLI already prepared the current input on every run.
- Make `review(bundle)` prepare its own input and create a fresh solver domain. Identical graph expressions and predicates can share results only inside that call. The reviewer stores no proof cache across requests. The preparation CLI and sampled checkpoint still use the same Stage 1 preparation function.
- Added four regressions that reuse the same bundle ID across reviews and change the requirements revision, approval state, workflow version and behavior, or ticket nullability. Each initial pass becomes an evidenced blocker when the new input changes the expected result. The full suite passed 79 tests.
