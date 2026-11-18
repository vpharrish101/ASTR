---
title: "ASTR AI assistance"
---

Codex materially assisted with assessment interpretation, architecture research,
implementation, refactoring, tests, evaluation, and documentation. The developer
chose the small requirements DSL, required library-first implementation and a
bounded scope, reviewed the plan, and ran the live Ollama development batches locally.

We checked the implementation and reports against the assessment PDF. Verification
included the independently authored routing oracle and frozen expected labels,
offline test runs, replay of reported counterexamples, and the frozen checkpoint,
final, and ablated evaluations. The custom code binds typed conditions to Z3 and
creates evidence-backed reports. Lark, Pydantic, graphlib, the Python AST library,
and Z3 supply parsing, validation, graph ordering, code inspection, and solving.
The engineering note preserves observed failures with their fixes and regressions.

The tested Ollama batch provides integration evidence alongside the offline
evaluation. Scripted fake-client tests have a separate purpose: validating
response checks and failure handling. Model suggestions never control approvals,
deterministic coverage, or final verdicts.
