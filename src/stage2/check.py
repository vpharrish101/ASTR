"""Exact routing comparison for the declared ticket domain."""

from collections import defaultdict
from graphlib import TopologicalSorter
from typing import Literal

import z3

from ..stage1.workflow import prepare
from ..utils.contracts import (
    BooleanField, BooleanGroup, CategoryField, Contract, Decision, Duration, Evidence, Issue, Presence, Route,
)
from .replay import OPERATORS, WorkflowPaths, matches, policy_path, rule_tables

NO_ROUTE, UNKNOWN = 0, -1
Result = Literal["equivalent", "different", "unresolved", "not_applicable"]
Ticket = dict[str, bool | int | str | None]


class Witness(Contract):
    ticket: Ticket
    expected_route: str | None
    actual_route: str | None
    expected_path: list[Evidence]
    actual_path: list[Evidence]
    expected_branch: bool | None = None
    actual_branch: bool | None = None


class Finding(Contract):
    code: str
    severity: Literal["blocker", "info"]
    rule: str | None = None
    evidence: list[Evidence]
    message: str
    correction: str
    witness: Witness | None = None


class Check(Contract):
    artifact: str
    result: Result
    rule: str | None = None
    detail: str | None = None


class Review(Contract):
    bundle: str
    status: Literal["PASS", "CHANGES_REQUIRED", "INCOMPLETE"]
    method: Literal["exact"] = "exact"
    summary: str
    findings: list[Finding]
    coverage: dict[str, dict[str, Result]]
    checks: list[Check]
    preparation_issues: list[Issue]


class Domain:
    def __init__(self, ticket_schema, timeout_ms):
        self.solver = z3.Solver()
        self.solver.set(timeout=timeout_ms)
        self.variables = {}
        self.categories = {}
        self.routes = {name: index for index, name in enumerate(ticket_schema.routes, 1)}
        for name, field in ticket_schema.fields.items():
            if isinstance(field, BooleanField):
                self.variables[name] = z3.Bool(name)
            else:
                variable = self.variables[name] = z3.Int(name)
                self.solver.add(variable >= 0)
                if isinstance(field, CategoryField):
                    options = self.categories[name] = field.choices + ([None] if field.nullable else [])
                    self.solver.add(variable < len(options))

    def predicate(self, condition):
        if isinstance(condition, BooleanGroup):
            combine = z3.And if condition.kind == "all" else z3.Or
            return combine(*(self.predicate(term) for term in condition.terms))
        variable = self.variables[condition.field]
        categories = self.categories.get(condition.field, [])
        if isinstance(condition, Presence):
            present = variable != categories.index(None) if None in categories else z3.BoolVal(True)
            return present if condition.present else z3.Not(present)
        value = condition.value
        if isinstance(value, Duration):
            value = value.seconds
        elif categories:
            value = categories.index(value)
        return OPERATORS[condition.op](variable, value)

    def search(self, query):
        result = self.solver.check(query)
        if result == z3.unknown:
            return "unresolved", None, self.solver.reason_unknown()
        if result == z3.unsat:
            return "equivalent", None, None
        model = self.solver.model()
        ticket = {}
        for name, variable in self.variables.items():
            value = model.eval(variable, model_completion=True)
            if z3.is_bool(variable):
                ticket[name] = z3.is_true(value)
            else:
                number = value.as_long()
                ticket[name] = self.categories[name][number] if name in self.categories else number
        return "different", ticket, None

    def policy(self, rules):
        remaining = z3.BoolVal(True)
        regions = []
        outcome = z3.IntVal(NO_ROUTE)
        for condition, route, _, _ in rules:
            guard = self.predicate(condition) if condition is not None else z3.BoolVal(True)
            regions.append(z3.And(remaining, guard))
            outcome = z3.If(regions[-1], self.routes[route], outcome)
            remaining = z3.And(remaining, z3.Not(guard))
        return outcome, regions

    def workflow(self, paths, linked_code=False):
        order = reversed(list(TopologicalSorter(paths.workflow.dependencies()).static_order()))
        reach = defaultdict(lambda: z3.BoolVal(False))
        reach[paths.workflow.entry] = z3.BoolVal(True)
        outcome = z3.IntVal(NO_ROUTE)
        for key in order:
            if key not in reach:
                continue
            _, node = paths.nodes[key]
            if isinstance(node, Route):
                outcome = z3.If(reach[key], self.routes[node.route], outcome)
                continue
            condition = paths.guard(node, linked_code)
            if condition is None:
                outcome = z3.If(reach[key], UNKNOWN, outcome)
                continue
            guard = self.predicate(condition)
            for target, branch in ((node.if_true, guard), (node.if_false, z3.Not(guard))):
                if target in paths.nodes:
                    reach[target] = z3.Or(reach[target], z3.And(reach[key], branch))
        return outcome, reach


LAYERS = ("spec", "workflow", "linked_code")


def rule_coverage(rules, source_issues, checks):
    ids = dict.fromkeys([*rules, *(issue.rule for issue in source_issues if issue.rule)])
    return {
        rule: {layer: (checks.get((layer, rule)) or checks[layer, None]).result for layer in LAYERS}
        for rule in ids
    }


def verdict(findings, checks, issues):
    blockers = sum(finding.severity == "blocker" for finding in findings)
    unresolved = len(issues) + sum(check.result == "unresolved" for check in checks)
    status = "CHANGES_REQUIRED" if blockers else "INCOMPLETE" if unresolved else "PASS"
    return status, f"{blockers} evidenced blocker(s), {unresolved} unresolved preparation issue(s) or check(s)."


def routing_finding(layer, ticket, expected, actual, rules, source_refs):
    want, winner, expected_path = expected
    route, refs, actual_path = actual
    rule = winner[0]
    rank, cited = rules[rule]
    if layer == "spec" and rule not in source_refs:
        code = "omitted_requirement"
    else:
        code = "route_mismatch"
        if layer != "spec" and route is None:
            code = "missing_branch"
        elif layer == "linked_code":
            code = "script_disagreement"
        # An early match can displace the rule that should have won.
        candidates = [rules[ref] for ref in refs if ref in rules
                      and rules[ref][0] < rank and rules[ref][1][1] == route]
        if candidates:
            _, cited = min(candidates, key=lambda item: item[0])
    cited_rule = cited[2][0]
    return Finding(
        code=code, severity="blocker", rule=cited_rule, evidence=cited[3] + actual_path,
        message=f"{layer} sends a ticket expected at {want} to {route or 'no route'}.",
        correction=f"Check the logic linked to {cited_rule} so this ticket reaches {want}.",
        witness=Witness(ticket=ticket, expected_route=want, actual_route=route,
                        expected_path=expected_path, actual_path=actual_path),
    )


def dead_end_finding(ticket, actual):
    route, refs, path = actual
    return Finding(
        code="missing_branch", severity="blocker", rule=refs[-1] if refs else None,
        evidence=path, message="A reachable workflow path ends without a route.",
        correction="Connect the indicated edge or entry to a valid destination.",
        witness=Witness(ticket=ticket, expected_route=None, actual_route=route, expected_path=[], actual_path=path),
    )


def script_finding(graph, node, index, snippet, rule, ticket):
    want, _, expected_path = graph.replay(ticket)
    route, _, actual_path = graph.replay(ticket, linked_code=True)
    return Finding(
        code="script_disagreement", severity="blocker", rule=rule,
        evidence=[graph.workflow.anchor(f"/nodes/{index}/when", node.when.model_dump_json()), snippet.evidence],
        message=f"Linked Python and decision {node.id} select different branches for this ticket.",
        correction=f"Make the Python predicate agree with the condition at {node.id} on reachable tickets.",
        witness=Witness(ticket=ticket, expected_route=want, actual_route=route,
                        expected_path=expected_path, actual_path=actual_path,
                        expected_branch=matches(node.when, ticket), actual_branch=matches(snippet.condition, ticket)),
    )


def review(bundle, *, timeout_ms=1000):
    prepared = prepare(bundle)
    domain = Domain(bundle.ticket, timeout_ms)
    expected, spec = rule_tables(bundle, prepared)
    graph = WorkflowPaths(bundle.workflow, prepared.snippets)
    rules = {row[2][0]: (index, row) for index, row in enumerate(expected)}
    source_refs = {ref for row in bundle.spec.rules for ref in row.source_rules}
    linked = {key: graph.snippets.get((node.code.id, node.code.version))
              for key, (_, node) in graph.nodes.items() if isinstance(node, Decision) and node.code}
    code_has_gaps = None in linked.values()
    findings = []
    checks = {}
    by_artifact = defaultdict(list)
    for issue in prepared.issues:
        by_artifact[(issue.evidence.artifact, issue.evidence.version)].append(issue)
    source_issues = (by_artifact[(bundle.requirements.id, bundle.requirements.version)]
                     + by_artifact[(bundle.approvals.id, bundle.approvals.version)])
    spec_ok = not {"unsupported_artifact", "ambiguous_spec_order"}.intersection(
        issue.code for issue in by_artifact[(bundle.spec.id, bundle.spec.version)])
    graph_ok = not {"unsupported_artifact", "workflow_cycle"}.intersection(
        issue.code for issue in by_artifact[(bundle.workflow.id, bundle.workflow.version)])
    expected_route, regions = domain.policy(expected) if not source_issues else (None, [])
    spec_route = domain.policy(spec)[0] if spec_ok else None
    workflow_route, reach = domain.workflow(graph) if graph_ok else (None, {})
    code_route, code_reach = domain.workflow(graph, linked_code=True) if graph_ok and linked else (None, {})
    # Reuse only the workflow checks built for this call's policy and ticket domain.
    same_routes = code_route is not None and code_route.eq(workflow_route)

    for layer, route, replay in (
        ("spec", spec_route, lambda ticket: policy_path(spec, ticket)),
        ("workflow", workflow_route, graph.replay),
        ("linked_code", code_route, lambda ticket: graph.replay(ticket, linked_code=True)),
    ):
        if route is None or source_issues:
            state = "not_applicable" if layer == "linked_code" and not linked else "unresolved"
            detail = "A supported source and implementation are required." if state == "unresolved" else None
            checks[layer, None] = Check(artifact=layer, result=state, detail=detail)
            continue

        for rule, region in zip(rules, regions, strict=True):
            if layer == "linked_code" and same_routes:
                checks[layer, rule] = checks["workflow", rule].model_copy(update={"artifact": layer})
                continue
            state, ticket, reason = domain.search(z3.And(region, route != UNKNOWN, route != expected_route))
            if code_has_gaps and layer == "linked_code" and state == "equivalent":
                gap, _, reason = domain.search(z3.And(region, route == UNKNOWN))
                if gap != "equivalent":
                    state, reason = "unresolved", reason or "A reachable code predicate is unresolved."
            if ticket is not None:
                predicted, actual = policy_path(expected, ticket), replay(ticket)
                if predicted[1] != [rule] or predicted[0] == actual[0]:
                    state, reason = "unresolved", "The solver witness did not replay as a routing difference."
                elif (layer != "linked_code" or checks["workflow", rule].result != "different"
                      or actual[0] != graph.replay(ticket)[0]):
                    findings.append(routing_finding(layer, ticket, predicted, actual, rules, source_refs))
            checks[layer, rule] = Check(artifact=layer, rule=rule, result=state, detail=reason)

    coverage = rule_coverage(rules, source_issues, checks)
    findings.extend(Finding(
        code="missing_rule_reference", severity="info", rule=rule.id,
        evidence=[rule.source, bundle.spec.anchor("/rules")],
        message=f"The specification has no source link for {rule.id}.",
        correction=f"Link the corresponding specification logic to {rule.id}.",
    ) for rule in prepared.effective_rules if rule.id not in source_refs and coverage[rule.id]["spec"] != "different")

    if graph_ok and source_issues:
        state, ticket, reason = domain.search(workflow_route == NO_ROUTE)
        checks["workflow_branches", None] = Check(artifact="workflow_branches", result=state, detail=reason)
        if ticket is not None:
            findings.append(dead_end_finding(ticket, graph.replay(ticket)))

    if graph_ok:
        for key, snippet in linked.items():
            if snippet is None:
                continue
            index, node = graph.nodes[key]
            declared, actual = domain.predicate(node.when), domain.predicate(snippet.condition)
            state, ticket, reason = ("equivalent", None, None) if declared.eq(actual) else domain.search(
                z3.And(z3.Or(reach[key], code_reach[key]), declared != actual))
            rule = node.source_rules[0] if len(node.source_rules) == 1 else None
            checks[f"code:{key}", rule] = Check(artifact=f"code:{key}", rule=rule, result=state, detail=reason)
            if ticket is not None:
                findings.append(script_finding(graph, node, index, snippet, rule, ticket))

    status, summary = verdict(findings, checks.values(), prepared.issues)
    return Review(bundle=bundle.id, status=status, summary=summary, findings=findings,
                    coverage=coverage, checks=list(checks.values()), preparation_issues=prepared.issues)
