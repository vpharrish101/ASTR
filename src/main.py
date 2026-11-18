"""Prepare one bundle and print versioned Stage 1 results as JSON."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from .stage1.workflow import prepare
from .utils.contracts import Bundle, Preparation


def unique_json_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("JSON objects must have unique keys")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, nargs="?")
    parser.add_argument("--review", action="store_true", help="Run exact Stage 2 semantic review")
    parser.add_argument("--schema", choices=["bundle", "preparation", "review"])
    args = parser.parse_args()
    if (args.schema is None) == (args.bundle is None):
        parser.error("provide a bundle path or --schema")
    if args.review and args.bundle is None:
        parser.error("--review requires a bundle path")
    if args.schema:
        if args.schema == "review":
            from .stage2.check import Review

            contract = Review
        else:
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
    if args.review:
        from .stage2.check import review

        report = review(bundle)
        print(report.model_dump_json(indent=2))
        return 0 if report.status == "PASS" else 1
    result = prepare(bundle)
    print(result.model_dump_json(indent=2))
    return 0 if result.status == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
