"""Replay one ticket through typed rules or the workflow, with evidence."""

import operator

from ..utils.contracts import BooleanGroup, Duration, Presence, Route

OPERATORS = {
    "==": operator.eq, "<": operator.lt, "<=": operator.le,
    ">": operator.gt, ">=": operator.ge,
}


def matches(condition, ticket):
    if isinstance(condition, BooleanGroup):
        combine = all if condition.kind == "all" else any
        return combine(matches(term, ticket) for term in condition.terms)
    value = ticket[condition.field]
    if isinstance(condition, Presence):
        return (value is not None) == condition.present
    target = condition.value
    if isinstance(target, Duration):
        target = target.seconds
    return OPERATORS[condition.op](value, target)


def policy_path(rules, ticket):
    """Rules are (condition, route, source IDs, evidence) in execution order."""
    path = []
    for condition, route, sources, anchors in rules:
        path.extend(anchors)
        if condition is None or matches(condition, ticket):
            return route, sources, path
    return None, [], path


def rule_tables(bundle, prepared):
    expected = [
        (rule.when, rule.route, [rule.id], [rule.source, *([rule.approval] if rule.approval else [])])
        for rule in prepared.effective_rules
    ]
    spec = [
        (rule.when, rule.route, rule.source_rules, [
            bundle.spec.anchor(f"/rules/{index}/{'route' if rule.when is None else 'when'}", rule.model_dump_json()),
        ])
        for index, rule in sorted(enumerate(bundle.spec.rules), key=lambda item: item[1].order)
    ]
    return expected, spec


class WorkflowPaths:
    def __init__(self, workflow, snippets=()):
        self.workflow = workflow
        self.nodes = {node.id: (index, node) for index, node in enumerate(workflow.nodes)}
        self.snippets = {(item.evidence.artifact, item.evidence.version): item for item in snippets}

    def guard(self, decision, linked_code):
        if linked_code and decision.code is not None:
            snippet = self.snippets.get((decision.code.id, decision.code.version))
            return snippet.condition if snippet else None
        return decision.when

    def replay(self, ticket, linked_code=False):
        current = self.workflow.entry
        anchor = self.workflow.anchor
        path = [anchor("/entry", current)]
        sources = []
        visited = set()
        while current in self.nodes and current not in visited:
            visited.add(current)
            index, node = self.nodes[current]
            loc = f"/nodes/{index}"
            if isinstance(node, Route):
                path.append(anchor(loc + "/route", node.route))
                return node.route, node.source_rules or sources, path
            sources = node.source_rules
            condition = self.guard(node, linked_code)
            if condition is None:
                break
            path.append(self.snippets[(node.code.id, node.code.version)].evidence
                        if linked_code and node.code else anchor(loc + "/when", condition.model_dump_json()))
            branch = matches(condition, ticket)
            current = node.if_true if branch else node.if_false
            path.append(anchor(loc + ("/if_true" if branch else "/if_false"), current))
        return None, sources, path
