# Stage 1 contracts and reference policy

## Requirements DSL

The versioned `requirements.text` field is the complete source of written policy. Blank lines are ignored. Each other line must be one complete rule. The DSL is case-sensitive. Rule IDs and ticket field and route names are ASCII identifiers. A source anchor is an artifact ID, a version, and a 1-based line and column in the decoded text.

```text
R1: when category is missing -> manual_review
R2: when fraud == true -> manual_review
R3: when urgent == true -> priority_queue
R4: when tier == "enterprise" and (category == "billing" or category == "technical") -> enterprise_queue
R5: when waiting >= 30 min -> escalation
R6: otherwise -> normal_queue
```

- Rules use first-match order and a single final `otherwise`. `and` binds more tightly than `or`, and parentheses override their precedence. An exception is simply a rule placed before a competing rule.
- Ticket fields come from the bundle schema. Boolean fields and duration fields are required. Category fields declare their allowed string values and may be nullable. A missing nullable category and explicit null have the same meaning. For predicates, tickets are normalized to contain every declared field, with a missing nullable category represented by `None`. Missing category compares unequal to every declared category string.
- Comparisons support `==`, plus `<`, `<=`, `>`, and `>=` for durations. Boolean literals are `true` and `false`. Category literals follow JSON string syntax. `is missing` and `is present` check presence explicitly.
- Tickets carry nonnegative integer seconds. Source duration literals use `sec` or `min`. Structured duration literals carry `amount` and `unit`. Both normalize exactly to seconds. Unsupported or ambiguous content keeps an issue with its evidence. Invalid lines and disputed rules must be accounted for before treating the remaining rules as a complete policy.

## Spec, graph, and code

A spec rule has its own ID, an explicit numeric order, a typed condition or null fallback, and a route. A workflow has an entry ID, decision nodes with true and false edges, and route terminals. Use an acyclic graph. Graph and spec record array order are serialization details. Each spec rule and workflow node may carry `source_rules` containing anchored source IDs. Local IDs can change. Evidence for an omission can always point back to the source policy.

A decision may link a Python snippet by exact artifact ID and version. JSON evidence uses JSON Pointer paths within the cited artifact. A Python evidence location uses its source line and column. The supported snippet shape is exactly one undecorated synchronous `def decide(ticket)` containing one return expression. The return expression uses direct reads like `ticket["waiting"]`, comparisons to typed literals, `is None` or `is not None` for presence, and nested `and` / `or`. Integer literals mean seconds. Imports, calls, arithmetic, loops, and chained comparisons are outside the subset. Comments are treated as comments. Stage 1 distinguishes Python syntax errors from unsupported AST content and never imports, evaluates, or executes a snippet.

Stage 1 verifies provenance, supported types, known source IDs, exact code links, and the graph's entry and acyclic structure. It preserves null edges, dangling targets, and unmapped source rules so the semantic stage can determine reachability and explain the effect. Artifact identity and version are scoped to the bundle. The CLI also rejects duplicate JSON keys.

## Approval authority

Only top-level records with `decision: "approved"` and a `target_revision` exactly matching the requirements version can change the effective source policy. A record replaces one known rule in place using one DSL statement with the same ID and fallback role. The log version identifies the evidence. Record array order is irrelevant.

For each targeted rule, `supersedes` can name applicable approved records for that same rule and revision. The relation must be acyclic and leave one unsuperseded record. Unlinked competing approvals leave the rule unresolved with evidence for every competitor. An invalid supersession or replacement also leaves the rule unresolved. The chosen replacement retains both the original source anchor and the approval anchor. Proposals, rejections, records for other revisions, and approval claims in prose or comments have no authority.

## Reference and evaluation design

The hand-authored reference makes R1 and R2 take priority over urgency. The enterprise exception applies after urgency and before general escalation. A wait approval targets v2 at 25 minutes. The base v1 policy stays at 30 minutes, even though its log contains a 20-minute proposal.

The [reference routing table](../data/expected/reference_routes.json) captures missing category, overlapping fraud and urgency, urgent enterprise tickets, both enterprise categories, fallback, and the exact 25- and 30-minute boundaries. The [independent oracle](../evaluation/reference_oracle.py) is hand-written from the business policy and has no imports from the reviewer. It checks the routing table and the expected side of the development witnesses. The [case inventory](../data/expected/cases.json) contains manually chosen final verdicts, defect locations, and sample witnesses. An equivalent witness may use different ticket values if it exposes the same evidenced disagreement.

| Split | Valid | Defective | Incomplete | Total |
|---|---:|---:|---:|---:|
| Development | 6 | 12 | 6 | 24 |
| Reserved | 2 | 4 | 2 | 8 |
| Total | 8 | 16 | 8 | 32 |

The renamed/reordered case and the strict-boundary case pair meaning-preserving and behavior-changing edits with the same reference. Other cases cover approval scope and supersession, units, wrong or missing edges, code mismatches, injected claims, and model failures. Named edits are deterministic and need no random seed. The reserved cases were authored for this project and are held out from reviewer development runs. The freeze hashes are checked without running the reserved cases.

The final evaluation should preserve a runnable early checkpoint with its source revision, compare checkpoint and final on the same frozen inputs, and ablate only the whole-policy interaction comparison. Preserve actual failures and focused regression tests as development proceeds. Report stage-limited results as preparation checks until the full reviewer can score final verdicts.
