# Stage 2: Compare what each implementation does

Stage 2 starts with the effective policy from Stage 1. In `check.py`, I build four routing decisions over the same set of ticket fields:

1. The **effective policy**, with applicable approvals already resolved.
2. The **structured spec**, evaluated in its declared rule order.
3. The **workflow graph**, using the conditions written on its nodes.
4. The **code-backed workflow**, using the parsed Python predicate wherever a node links to a snippet.

## How the comparison works

`Domain` gives Z3 a symbolic ticket. Boolean fields are Boolean variables, categories are limited to their declared values, and a nullable category has a distinct missing value. Waiting time is a nonnegative integer in seconds, so `30 min` and `1800 sec` represent the same threshold.

For an ordered policy, a rule can win only when **its condition is true and every earlier condition is false**. That’s how the checker accounts for precedence. For the workflow, `graphlib.TopologicalSorter` gives an order in which to propagate reachability from the entry node. Each decision sends reachable tickets down its true or false edge. A reachable path without a destination has a **no-route** outcome. A path that depends on an unavailable Python predicate has an **unresolved** outcome.

For each rule and implementation, the checker asks Z3 whether a valid ticket can reach that rule and take a different route. If Z3 finds one, I replay it through the policy and the affected artifact to confirm the difference and collect the evidence. If Z3 proves the query unsatisfiable, the routes agree within that part of the supported domain. A solver timeout leaves the check unresolved.

There’s also a check for whether linked Python and its declared workflow condition can choose different branches on a ticket that can reach the node. That can reveal a predicate mismatch even when both branches eventually lead to the same queue.

## Worked example

In [`d09_exclusive_boundary`](../data/development/d09_exclusive_boundary.json), R5 says `>= 30 min`, while the spec says `> 30 min`. The ticket Z3 found is a standard, non-urgent, non-fraud ticket in the general category that has waited exactly **1,800 seconds**.

| Evidence | What the report shows |
|---|---|
| Effective policy | `escalation`, from `support_policy` v1, `line 5:1` |
| Spec | `normal_queue`, with the faulty condition at `support_spec` v1, `/rules/4/when` |
| Finding | `route_mismatch`, a blocker tied to R5 |

The report keeps the replay paths under `findings` and the per-rule results under `coverage` and `checks`. The symbolic checks are in [`src/stage2/check.py`](../src/stage2/check.py). Concrete replay is in [`src/stage2/replay.py`](../src/stage2/replay.py).
