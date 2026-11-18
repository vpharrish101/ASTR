"""Prepare one bundle and print versioned Stage 1 results as JSON."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from .stage1.policy import resolve_policy
from .stage1.workflow import inspect_artifacts
from .utils.contracts import Bundle, Preparation


def prepare(bundle: Bundle) -> Preparation:
    rules, source_ids, issues = resolve_policy(bundle)
    snippets, artifact_issues = inspect_artifacts(bundle, source_ids)
    issues.extend(artifact_issues)
    return Preparation(
        bundle=bundle.id, status="UNRESOLVED" if issues else "READY",
        source_revision=bundle.requirements.version, effective_rules=rules,
        snippets=snippets, issues=issues,
    )


def unique_json_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("JSON objects must have unique keys")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, nargs="?")
    parser.add_argument("--schema", choices=["bundle", "preparation"])
    args = parser.parse_args()
    if (args.schema is None) == (args.bundle is None):
        parser.error("provide a bundle path or --schema")
    if args.schema:
        contract = Bundle if args.schema == "bundle" else Preparation
        print(json.dumps(contract.model_json_schema(), indent=2))
        return 0
    try:
        text = args.bundle.read_bytes()
    except OSError as exc:
        print(json.dumps({"error": "unreadable_bundle", "message": str(exc)}))
        return 2
    try:
        bundle = Bundle.model_validate(json.loads(text, object_pairs_hook=unique_json_object))
    except ValidationError as exc:
        print(json.dumps({"error": "invalid_bundle", "details": [
            {"location": detail["loc"], "message": detail["msg"]}
            for detail in exc.errors(include_input=False, include_url=False)
        ]}))
        return 2
    except ValueError as exc:
        print(json.dumps({"error": "invalid_bundle", "message": str(exc)}))
        return 2
    result = prepare(bundle)
    print(result.model_dump_json(indent=2))
    return 0 if result.status == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
