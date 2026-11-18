"""Prepare or review one versioned workflow bundle and print a JSON result."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from .stage1.workflow import prepare
from .utils.contracts import Bundle, Preparation, decode_json, read_bounded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, nargs="?")
    parser.add_argument("--review", action="store_true", help="Run semantic review")
    model = parser.add_mutually_exclusive_group()
    model.add_argument("--fake-script", type=Path, help="Require a model review using this fake-client script")
    model.add_argument("--ollama-model", choices=["qwen3:4b", "qwen3:8b"], help="Require local Ollama review")
    parser.add_argument("--schema", choices=["bundle", "preparation", "review", "assisted-review"])
    args = parser.parse_args()
    if (args.schema is None) == (args.bundle is None):
        parser.error("provide a bundle path or --schema")
    if args.review and args.bundle is None:
        parser.error("--review requires a bundle path")
    required = bool(args.fake_script or args.ollama_model)
    if required and not args.review:
        parser.error("model options require --review")
    if args.schema:
        if args.schema == "review":
            from .stage2.contracts import Review

            contract = Review
        elif args.schema == "assisted-review":
            from .stage3.model_review import AssistedReview

            contract = AssistedReview
        else:
            contract = Bundle if args.schema == "bundle" else Preparation
        print(json.dumps(contract.model_json_schema(), indent=2))
        return 0

    try:
        if args.review:
            from .stage3.model_review import BUNDLE_BYTES, OllamaClient, assisted_review, unreviewed

            raw = read_bounded(args.bundle, BUNDLE_BYTES)
        else:
            raw = args.bundle.read_bytes()
        bundle = Bundle.model_validate(decode_json(raw))
    except OSError as exc:
        error = {"error": "unreadable_bundle", "message": str(exc)}
    except ValidationError as exc:
        error = {"error": "invalid_bundle", "details": [
            {"location": detail["loc"], "message": detail["msg"]}
            for detail in exc.errors(include_input=False, include_url=False)
        ]}
    except (ValueError, RecursionError) as exc:
        error = {"error": "invalid_bundle", "message": str(exc)}
    else:
        if args.review:
            client = OllamaClient(args.ollama_model) if args.ollama_model else None
            result = assisted_review(bundle, client, script_path=args.fake_script)
        else:
            result = prepare(bundle)
        print(result.model_dump_json(indent=2))
        return int(result.status not in {"READY", "PASS"})

    if args.review:
        report = unreviewed(args.bundle.name, json.dumps(error), required=required)
        print(report.model_dump_json(indent=2))
    else:
        print(json.dumps(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
