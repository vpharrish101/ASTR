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
