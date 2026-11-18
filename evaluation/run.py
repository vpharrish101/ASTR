"""Run and score the frozen CPU-only comparison with zero model calls."""

import argparse
import json
import platform
import subprocess
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path
from time import perf_counter

from src.main import prepare
from src.stage2.replay import WorkflowPaths, policy_path, rule_tables
from src.stage3.model_review import assisted_review
from src.utils.contracts import Bundle

from .checkpoint import review_samples
from .reference_oracle import reference_route

ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "evaluation/implementation-freeze.json"
STATES = ("PASS", "CHANGES_REQUIRED", "INCOMPLETE")


def fingerprint(paths):
    return {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def verify_freeze(split, freeze):
    dataset = json.loads((ROOT / "evaluation/freeze.json").read_text())
    files = [ROOT / name for name in dataset["sha256"]]
    if fingerprint(files) != dataset["sha256"]:
        raise SystemExit("Dataset or label freeze mismatch")
    implementation = fingerprint([
        *ROOT.glob("src/**/*.py"), *ROOT.glob("config/*"), *ROOT.glob("evaluation/*.py"),
        *ROOT.glob("tests/test_stage[12].py"), ROOT / "evaluation/freeze.json", ROOT / "requirements.txt",
        ROOT / ".python-version",
    ])
    if freeze:
        if FREEZE.exists():
            raise SystemExit("Implementation freeze already exists")
        with FREEZE.open("x") as file:
            json.dump(implementation, file, indent=2)
            file.write("\n")
    if split != "development" and (not FREEZE.exists() or json.loads(FREEZE.read_text()) != implementation):
        raise SystemExit("The reserved run requires a matching implementation freeze")
    return implementation


def score(bundle, label, report):
    prepared = prepare(bundle)
    graph = WorkflowPaths(bundle.workflow, prepared.snippets)
    _, spec = rule_tables(bundle, prepared)
    artifacts = {(item.id, item.version): item.model_dump()
                 for item in (bundle.requirements, bundle.approvals, bundle.spec, bundle.workflow, *bundle.snippets)}
    snippets = {(s.evidence.artifact, s.evidence.version, s.evidence.location): s.evidence.text
                for s in prepared.snippets}
    expected_location, witnesses, unsupported = False, 0, 0
    for finding in report["findings"]:
        anchors = finding["evidence"]
        anchor = label.get("evidence")
        blocker = finding.get("severity", "blocker") == "blocker"
        supported = bool(anchors)
        for ref in anchors:
            artifact = artifacts.get((ref["artifact"], ref["version"]), {})
            loc = ref["location"]
            if loc.startswith("/"):
                try:
                    for part in loc.split("/")[1:]:
                        artifact = artifact[int(part)] if isinstance(artifact, list) else artifact[part]
                    found = True
                except (KeyError, IndexError, TypeError, ValueError):
                    found = False
            elif loc.startswith("record "):
                found = any(record["id"] == loc[7:] and record["replacement"] == ref.get("text")
                            for record in artifact.get("records", []))
            elif "text" in artifact and loc.startswith("line "):
                lines = artifact["text"].splitlines()
                number = int(loc.split()[1].split(":")[0])
                found = 0 < number <= len(lines) and lines[number - 1] == ref.get("text")
            else:
                key = ref["artifact"], ref["version"], loc
                found = key in snippets and snippets[key] == ref.get("text")
            supported &= found
        witness = finding.get("witness") or (finding if "ticket" in finding else None)
        if blocker and witness:
            ticket, path = witness["ticket"], witness.get("actual_path", [])
            if any(ref["artifact"] == bundle.spec.id for ref in path):
                actual = policy_path(spec, ticket)[0]
            else:
                linked = any((ref["artifact"], ref["version"], ref["location"]) in snippets for ref in path)
                actual = graph.replay(ticket, linked_code=linked)[0]
            expected = reference_route(ticket, label.get("effective_wait_seconds", 1800))[1]
            useful = expected == witness["expected_route"] and actual == witness["actual_route"] and actual != expected
            witnesses += bool(supported and useful)
            supported &= useful
        elif blocker:
            supported = False
        expected_location |= bool(supported and blocker and anchor and finding["rule"] == label.get("rule")
                                  and any(all(ref.get(k) == v for k, v in anchor.items()) for ref in anchors))
        unsupported += not supported
    return {"expected_defect_location": expected_location, "useful_witnesses": witnesses,
            "unsupported_findings": unsupported, "findings": len(report["findings"])}


def summarize(rows):
    counts = Counter(row["status"] for row in rows)
    expected = Counter(row["expected"] for row in rows)
    def outcomes(want, got):
        return sum(row["expected"] == want and row["status"] == got for row in rows)

    return {
        "bundles": len(rows), "verdicts": {state: counts[state] for state in STATES},
        "expected_verdicts": {state: expected[state] for state in STATES},
        "matched_verdicts": sum(row["status"] == row["expected"] for row in rows),
        "defective_detected": outcomes("CHANGES_REQUIRED", "CHANGES_REQUIRED"),
        "false_passes": outcomes("CHANGES_REQUIRED", "PASS"),
        "false_blocks": outcomes("PASS", "CHANGES_REQUIRED"),
        "incomplete_by_case_type": {state: outcomes(state, "INCOMPLETE") for state in STATES},
        "expected_defect_locations_found": sum(row["expected_defect_location"] for row in rows),
        **{key: sum(row[key] for row in rows) for key in ("useful_witnesses", "unsupported_findings", "findings")},
        "wall_seconds": round(sum(row["elapsed"] for row in rows), 6), "model_calls": 0, "model_retries": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["development", "all"], default="all")
    parser.add_argument("--freeze", action="store_true", help="Record the final code before any reserved run")
    parser.add_argument("--verify", action="store_true", help="Run Stage 1 and Stage 2 tests before scoring")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation/results/offline.json")
    args = parser.parse_args()
    implementation = verify_freeze(args.split, args.freeze)
    if args.verify:
        subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_stage1.py", "tests/test_stage2.py"],
                       cwd=ROOT, check=True)
    labels = json.loads((ROOT / "data/expected/cases.json").read_text())["cases"]
    variants = {
        "checkpoint": review_samples,
        "final": lambda bundle: assisted_review(bundle).model_dump(),
        "ablation": lambda bundle: assisted_review(bundle, compare_routes=False).model_dump(),
    }
    data = {}
    for split in ("development", "reserved") if args.split == "all" else ("development",):
        data[split] = {mode: [] for mode in variants}
        for path in sorted((ROOT / "data" / split).glob("*.json")):
            label = labels[path.stem]
            expected = "PASS" if label.get("model_failure") else label["status"]
            for mode, run in variants.items():
                bundle = Bundle.model_validate_json(path.read_bytes())
                start = perf_counter()
                report = run(bundle)
                elapsed = perf_counter() - start
                data[split][mode].append({
                    "bundle": bundle.id, "status": report["status"], "expected": expected,
                    "frozen_label": label["status"], "model_failure_label": label.get("model_failure"),
                    "elapsed": elapsed, **score(bundle, label, report), "report": report,
                })
    metrics = {split: {mode: summarize(rows) for mode, rows in batch.items()} for split, batch in data.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "mode": "offline", "python": platform.python_version(), "architecture": platform.machine(),
        "implementation_sha256": implementation,
        "expectations": "The four scripted model-failure cases have valid routing and expect PASS in zero-model mode.",
        "metrics": metrics, "cases": data,
    }, indent=2) + "\n")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
