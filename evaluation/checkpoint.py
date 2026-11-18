"""Initial sampled reviewer. Use the same frozen bundle paths as the exact reviewer."""

import argparse
import json
from itertools import combinations, product
from pathlib import Path

from src.main import prepare
from src.stage2.replay import WorkflowPaths, policy_path, rule_tables
from src.utils.contracts import BooleanField, Bundle, CategoryField


def review_samples(bundle):
    prepared = prepare(bundle)
    if prepared.issues:
        return {"status": "INCOMPLETE", "method": "sampled", "samples": 0,
                "issues": [issue.model_dump() for issue in prepared.issues], "findings": []}

    values = {}
    for name, field in bundle.ticket.fields.items():
        if isinstance(field, BooleanField):
            values[name] = [False, True]
        elif isinstance(field, CategoryField):
            values[name] = field.choices + ([None] if field.nullable else [])
        else:
            values[name] = [0, 1499, 1500, 1799, 1800, 1801, 2400]
    default = {name: options[0] for name, options in values.items()}
    tickets = [default] + [
        {**default, name: value}
        for name, options in values.items() for value in options[1:]
    ]
    tickets.extend(
        {**default, first: left, second: right}
        for first, second in combinations(values, 2)
        for left, right in product(values[first][1:], values[second][1:])
    )
    rules, _ = rule_tables(bundle, prepared)
    graph = WorkflowPaths(bundle.workflow)
    findings = []
    for ticket in tickets:
        expected, sources, source_path = policy_path(rules, ticket)
        actual, _, actual_path = graph.replay(ticket)
        if expected != actual:
            findings.append({
                "ticket": ticket, "expected_route": expected, "actual_route": actual,
                "rule": sources[0] if sources else None,
                "evidence": [anchor.model_dump() for anchor in source_path + actual_path],
            })
    return {
        "status": "CHANGES_REQUIRED" if findings else "INCOMPLETE",
        "method": "sampled", "samples": len(tickets), "findings": findings,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    print(json.dumps(review_samples(Bundle.model_validate_json(args.bundle.read_bytes())), indent=2))
