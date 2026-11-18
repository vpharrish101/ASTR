---
title: "ASTR engineering note"
---

## Scope and implementation

ASTR reviews versioned synthetic support-ticket bundles. A Lark grammar parses
anchored source rules. Pydantic validates inputs and reports. The policy supports
ordered first-match rules, missing categorical values, equality, numeric comparisons,
integer seconds and conversion from minutes, and nested AND/OR. Python's AST parser
accepts a bounded predicate subset. The report records syntax parsing, subset inspection,
and execution separately. Linked Python is never executed.

An applicable approved revision can replace a source rule at the same precedence
position. Explicit supersession determines the active replacement. A conflict or
unsupported rule keeps its evidence and leaves the affected scope unresolved.
Approval text and code comments are data, with approval authority in top-level metadata.

Stage 2 lowers the policy, spec, workflow, and code-backed workflow independently.
The graph uses `graphlib` and a forward traversal that propagates Boolean reachability
and assigns outcomes to reachable terminals. Unmatched paths have no route, and
paths blocked by unavailable code stay unresolved. For each first-match source
region, Z3 searches for a differing route over the declared ticket domain.
Unsatisfiability establishes equivalence for that region. A satisfying result is
replayed to produce the ticket, routes, and versioned evidence. Linked predicates
also get a separate reachable-branch comparison.

Stage 3 leaves approval resolution and verdicts with the application. The model
supplies one typed route claim and exact quote per rule, and can suggest human
follow-up on wording or precedence. A strict validator checks the request digest,
rule coverage, citation, quote, and route. The model cannot remove a deterministic
blocker. Limits cover request and response bytes, token usage in the Ollama adapter,
the shared deadline, and at most two attempts. The fake adapter exercises failure
handling. Live results measure integration in an actual advisory review.

## Frozen comparison

The 32 bundles derive from a hand-authored reference and named variations. No random
seed is needed. Manual labels and the independent reference oracle live outside
reviewer inputs. The initial sampled checkpoint checks a default ticket and
one-field and two-field variations. The eight reserved bundles were kept out of
development reviewer runs. Input and implementation hashes were checked before
scoring the reserved split. No checker changes followed that run.

| Split | Run | Correct verdicts | Defects found | Expected locations | Incomplete |
|---|---|---:|---:|---:|---:|
| Development | Checkpoint | 9/24 | 6/12 | 5/12 | 18 |
| Development | Final | 24/24 | 12/12 | 12/12 | 3 |
| Development | Ablation | 5/24 | 2/12 | 2/12 | 22 |
| Reserved | Checkpoint | 4/8 | 3/4 | 3/4 | 5 |
| Reserved | Final | 8/8 | 4/4 | 4/4 | 1 |
| Reserved | Ablation | 3/8 | 2/4 | 2/4 | 6 |

All configurations made zero model calls and retries. There were zero false passes
among 16 defective bundles and zero false blocks among 12 semantically valid
bundles in each configuration. The checkpoint and ablation abstained on all 12
valid bundles. The final review abstained on all four genuinely unresolved
deterministic cases. Four additional bundles contain scripted model failures and
have valid routing. Their frozen labels remain in the output and their deterministic
expectation is PASS for this zero-model experiment.

The ablation disables whole-policy route comparison only. It preserves the
reachable dead-end and linked predicate checks, which still find four defects.
The other 12 defects require whole-policy comparison in this implementation.
For example, `d10` places urgency before missing category. Its individual
conditions look plausible, but a missing-category urgent ticket takes the wrong
route. Skipped comparisons are explicitly unresolved.

On the frozen run, summed reviewer wall times across 32 cases were 0.129 seconds
for the checkpoint, 0.444 seconds for the final reviewer, and 0.197 seconds for the
ablation. Measurements exclude report scoring and process startup. The final
reviewer returned 22 replayable blocker witnesses and cited the designated location
on all 16 defective bundles. The automated evidence rubric resolved cited anchors
and replayed each claimed route difference. It counted zero unsupported findings
in all three configurations. Checkpoint witnesses can repeat the same underlying
defect across multiple sampled tickets.

## Real-model development evidence

The separate local Qwen3 4B development run matched 24/24 live-mode verdicts and
detected 12/12 defects. Ollama attempted 21 bundles. Twenty passed model validation.
Both attempts on `d15` failed exact-quote validation and its deterministic blocker
survived. There were 22 calls, one retry, and 6.542 seconds median call latency.
The three deterministic incomplete cases never called the model. The reports
did not establish actual GPU offload or the Ollama version. Live results cover the
development split. They establish integration behavior for the supplied run.

## Two debugging examples

1. **Counterexample attribution.** Initial output for `d07` showed
   `route_mismatch:R6` twice. The threshold defect belonged to R5. An attempted
   fix then counted bypassed graph conditions as candidates. Two regression tests
   failed, including `test_workflow_trace_does_not_blame_a_condition_that_was_bypassed`
   with `assert False`. The fix uses the selected spec rule or the decision feeding
   the workflow terminal. The replay still retains the complete path.
2. **Stale input at the Python API.** Revising the source to v2 while passing
   the previous preparation produced `Changed revision with old preparation: PASS`.
   Fresh preparation gave `CHANGES_REQUIRED` because the 25-minute approval became
   applicable. The reviewer now takes a bundle, prepares it internally, and creates
   a fresh solver. Regressions change the revision, approval state, workflow, and
   ticket domain while retaining the bundle ID.

This evidence covers the declared DSL, supported Python subset and synthetic cases.
General Python, arbitrary prose, and statistical generalization beyond these cases
remain outside the evaluation.
