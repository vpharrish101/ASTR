"""Validate artifact links and inspect restricted Python without executing it."""

import ast
from collections import defaultdict
from graphlib import CycleError, TopologicalSorter

from ..utils.contracts import (
    BooleanGroup, Bundle, Comparison, Condition, Decision, Duration, Issue,
    ParsedSnippet, Preparation, Presence,
)
from .policy import condition_error, resolve_policy, rule_error

OPERATORS = {ast.Eq: "==", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}


def python_condition(node: ast.expr) -> Condition:
    match node:
        case ast.BoolOp(op=op, values=terms):
            return BooleanGroup(
                kind="all" if isinstance(op, ast.And) else "any",
                terms=[python_condition(term) for term in terms],
            )
        case ast.Compare(
            left=ast.Subscript(value=ast.Name(id="ticket"), slice=ast.Constant(value=str() as field)),
            ops=[op], comparators=[ast.Constant(value=value)],
        ):
            if value is None and isinstance(op, (ast.Is, ast.IsNot)):
                return Presence(field=field, present=isinstance(op, ast.IsNot))
            if type(op) in OPERATORS and type(value) in (bool, str, int):
                if type(value) is int:
                    value = Duration(amount=value, unit="sec")
                return Comparison(field=field, op=OPERATORS[type(op)], value=value)
    raise ValueError("Expected a supported ticket comparison or and/or expression")


def predicate_expression(source: str) -> ast.expr:
    match ast.parse(source):
        case ast.Module(body=[ast.FunctionDef(
            name="decide",
            args=ast.arguments(
                posonlyargs=[], args=[ast.arg(arg="ticket", annotation=None)], vararg=None,
                kwonlyargs=[], kwarg=None, defaults=[],
            ),
            body=[ast.Return(value=expression)], decorator_list=[], returns=None, type_params=[],
        )]) if expression is not None:
            return expression
    raise ValueError("Expected exactly def decide(ticket) with one return expression")


def inspect_artifacts(bundle: Bundle, source_ids: set[str]) -> tuple[list[ParsedSnippet], list[Issue]]:
    issues: list[Issue] = []
    spec, graph = bundle.spec, bundle.workflow
    if len({rule.order for rule in spec.rules}) != len(spec.rules):
        issues.append(spec.issue("ambiguous_spec_order", "Specification rule order values must be unique", "/rules"))

    dependencies = graph.dependencies()
    if graph.entry not in dependencies:
        issues.append(graph.issue("unknown_entry", f"Missing workflow entry node {graph.entry!r}", "/entry"))
    snippet_keys = {(snippet.id, snippet.version) for snippet in bundle.snippets}
    code_rules = defaultdict(set)

    for artifact, entries, collection in [(spec, spec.rules, "rules"), (graph, graph.nodes, "nodes")]:
        for index, entry in enumerate(entries):
            loc = f"/{collection}/{index}"
            issues.extend(artifact.issue("unknown_source_rule", f"No anchored source rule {ref!r}", loc, rule=ref)
                          for ref in sorted(set(entry.source_rules) - source_ids))
            if isinstance(entry, Decision):
                error = condition_error(entry.when, bundle.ticket)
                if entry.code is not None:
                    key = (entry.code.id, entry.code.version)
                    code_rules[key].update(entry.source_rules)
                    if key not in snippet_keys:
                        issues.append(artifact.issue(
                            "missing_snippet", f"Missing snippet {entry.code.id}@{entry.code.version}", loc + "/code",
                            rule=entry.source_rules[0] if len(entry.source_rules) == 1 else None,
                        ))
            else:
                error = rule_error(entry, bundle.ticket)
            if error is not None:
                issues.append(artifact.issue("unsupported_artifact", error, loc))

    try:
        TopologicalSorter(dependencies).prepare()
    except CycleError:
        issues.append(graph.issue("workflow_cycle", "The supported workflow graph must be acyclic", "/nodes"))

    parsed: list[ParsedSnippet] = []
    for snippet in bundle.snippets:
        evidence = snippet.anchor("line 1:1", snippet.source)
        refs = code_rules[(snippet.id, snippet.version)]
        rule = next(iter(refs)) if len(refs) == 1 else None
        try:
            expression = predicate_expression(snippet.source)
            evidence = snippet.anchor(f"line {expression.lineno}:{expression.col_offset + 1}",
                                      ast.get_source_segment(snippet.source, expression))
            condition = python_condition(expression)
            if error := condition_error(condition, bundle.ticket):
                raise ValueError(error)
        except SyntaxError as exc:
            evidence = snippet.anchor(f"line {exc.lineno or 1}:{exc.offset or 1}", exc.text)
            issues.append(Issue(code="python_syntax", message=exc.msg, evidence=evidence, rule=rule))
        except ValueError as exc:
            issues.append(Issue(code="unsupported_python", message=str(exc), evidence=evidence, rule=rule))
        else:
            parsed.append(ParsedSnippet(condition=condition, evidence=evidence))
    return parsed, issues


def prepare(bundle: Bundle) -> Preparation:
    rules, source_ids, issues = resolve_policy(bundle)
    snippets, artifact_issues = inspect_artifacts(bundle, source_ids)
    issues.extend(artifact_issues)
    return Preparation(
        bundle=bundle.id, status="UNRESOLVED" if issues else "READY",
        source_revision=bundle.requirements.version, effective_rules=rules,
        snippets=snippets, issues=issues,
    )
