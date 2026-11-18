"""Validate artifact links and inspect restricted Python without executing it."""

import ast
from collections import defaultdict
from graphlib import CycleError, TopologicalSorter

from ..utils.contracts import (
    BooleanGroup, Bundle, Comparison, Condition, Decision, Duration, Evidence, Issue,
    ParsedSnippet, Presence,
)
from .policy import condition_error, rule_error

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
    spec_orders = {rule.order for rule in spec.rules}
    if len(spec_orders) != len(spec.rules):
        issues.append(Issue(
            code="ambiguous_spec_order", message="Specification rule order values must be unique",
            evidence=Evidence(artifact=spec.id, version=spec.version, location="/rules"),
        ))

    nodes = {node.id: node for node in graph.nodes}
    if graph.entry not in nodes:
        issues.append(Issue(
            code="unknown_entry", message=f"Missing workflow entry node {graph.entry!r}",
            evidence=Evidence(artifact=graph.id, version=graph.version, location="/entry"),
        ))
    edges = {}
    snippet_keys = {(snippet.id, snippet.version) for snippet in bundle.snippets}
    code_rules = defaultdict(set)

    for artifact, entries, collection in [(spec, spec.rules, "rules"), (graph, graph.nodes, "nodes")]:
        for index, entry in enumerate(entries):
            evidence = Evidence(
                artifact=artifact.id, version=artifact.version, location=f"/{collection}/{index}",
            )
            for ref in sorted(set(entry.source_rules) - source_ids):
                issues.append(Issue(
                    code="unknown_source_rule", message=f"No anchored source rule {ref!r}",
                    evidence=evidence, rule=ref,
                ))
            if isinstance(entry, Decision):
                error = condition_error(entry.when, bundle.ticket)
                edges[entry.id] = [
                    target for target in (entry.if_true, entry.if_false) if target in nodes
                ]
                if entry.code is not None:
                    key = (entry.code.id, entry.code.version)
                    code_rules[key].update(entry.source_rules)
                    if key not in snippet_keys:
                        issues.append(Issue(
                            code="missing_snippet", message=f"Missing snippet {entry.code.id}@{entry.code.version}",
                            evidence=evidence.model_copy(update={"location": evidence.location + "/code"}),
                            rule=entry.source_rules[0] if len(entry.source_rules) == 1 else None,
                        ))
            elif artifact is spec:
                error = rule_error(entry, bundle.ticket)
            else:
                error = None if entry.route in bundle.ticket.routes else f"Unknown route {entry.route!r}"
            if error is not None:
                issues.append(Issue(code="unsupported_artifact", message=error, evidence=evidence))

    try:
        tuple(TopologicalSorter(edges).static_order())
    except CycleError:
        issues.append(Issue(
            code="workflow_cycle", message="The supported workflow graph must be acyclic",
            evidence=Evidence(artifact=graph.id, version=graph.version, location="/nodes"),
        ))

    parsed: list[ParsedSnippet] = []
    for snippet in bundle.snippets:
        evidence = Evidence(
            artifact=snippet.id, version=snippet.version, location="line 1:1", text=snippet.source,
        )
        refs = code_rules[(snippet.id, snippet.version)]
        rule = next(iter(refs)) if len(refs) == 1 else None
        try:
            expression = predicate_expression(snippet.source)
            evidence.location = f"line {expression.lineno}:{expression.col_offset + 1}"
            evidence.text = ast.get_source_segment(snippet.source, expression)
            condition = python_condition(expression)
        except SyntaxError as exc:
            evidence.location = f"line {exc.lineno or 1}:{exc.offset or 1}"
            evidence.text = exc.text
            issues.append(Issue(code="python_syntax", message=exc.msg, evidence=evidence, rule=rule))
        except ValueError as exc:
            issues.append(Issue(
                code="unsupported_python", message=str(exc), evidence=evidence, rule=rule,
            ))
        else:
            if error := condition_error(condition, bundle.ticket):
                issues.append(Issue(code="unsupported_python", message=error, evidence=evidence, rule=rule))
            else:
                parsed.append(ParsedSnippet(condition=condition, evidence=evidence))
    return parsed, issues
