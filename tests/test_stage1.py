import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from evaluation.reference_oracle import reference_route
from src.main import prepare
from src.utils.contracts import Bundle, Preparation

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REFERENCE = DATA / "development" / "d01_reference.json"
LABELS = json.loads((DATA / "expected" / "cases.json").read_text())
DEVELOPMENT = sorted((DATA / "development").glob("*.json"))


@pytest.fixture
def reference():
    return Bundle.model_validate_json(REFERENCE.read_bytes())


@pytest.mark.parametrize("path", DEVELOPMENT, ids=lambda path: path.stem)
def test_development_preparation(path):
    result = prepare(Bundle.model_validate_json(path.read_bytes()))
    label = LABELS["cases"][path.stem]
    if code := label.get("stage1_issue"):
        assert result.status == "UNRESOLVED"
        assert any(
            issue.code == code
            and issue.rule == label["rule"]
            and issue.evidence.artifact == label["evidence"]["artifact"]
            and issue.evidence.version == label["evidence"]["version"]
            and issue.evidence.location.startswith(label["evidence"]["location"])
            for issue in result.issues
        )
    else:
        assert result.status == "READY", result.issues


@pytest.mark.parametrize(("case", "seconds", "approval"), [
    ("d01_reference", 1800, None),
    ("d02_approved_revision", 1500, "record approve_25_next"),
    ("d05_superseded_approval", 1500, "record final"),
    ("d07_unapproved_threshold", 1800, None),
    ("d15_ignored_approval", 1500, "record approve_25_next"),
])
def test_effective_approvals_preserve_source(case, seconds, approval):
    bundle = Bundle.model_validate_json((DATA / "development" / f"{case}.json").read_bytes())
    result = prepare(bundle)
    rule = next(rule for rule in result.effective_rules if rule.id == "R5")
    assert rule.when.value.seconds == seconds
    assert rule.source.location == "line 5:1"
    assert rule.source.version == bundle.requirements.version
    assert "30 min" in rule.source.text
    assert (rule.approval.location if rule.approval else None) == approval


def test_conflict_retains_both_records_and_neither_replacement():
    bundle = Bundle.model_validate_json(
        (DATA / "development" / "d19_conflicting_approvals.json").read_bytes()
    )
    result = prepare(bundle)
    assert "R5" not in {rule.id for rule in result.effective_rules}
    assert {issue.evidence.location for issue in result.issues} == {
        "record approve_25_next", "record approve_24",
    }


def test_dsl_anchors_typing_and_precedence(reference):
    reference.requirements.text = (
        'R1 : when urgent == true or fraud == true and (waiting <= 31 min or tier == "enterprise")'
        ' -> priority_queue\nR6: otherwise -> normal_queue\n'
    )
    first = prepare(reference).effective_rules[0]
    assert first.source.location == "line 1:1"
    assert first.when.kind == "any"
    assert first.when.terms[1].kind == "all"
    assert first.when.terms[1].terms[1].terms[0].value.seconds == 1860


@pytest.mark.parametrize("line", [
    "R1 : when category is missing -> manual_review",
    "R1: when invented == true -> manual_review",
    "R1: when waiting >= 20 -> escalation",
    "R1: when waiting >= 20 hours -> escalation",
])
def test_unsupported_or_duplicate_lines_remain_visible(reference, line):
    reference.requirements.text += line + "\n"
    result = prepare(reference)
    assert result.status == "UNRESOLVED"
    assert any(
        issue.evidence.text == line and issue.evidence.location.startswith("line 7:")
        for issue in result.issues
    )
    assert "R1" not in {rule.id for rule in result.effective_rules}


@pytest.mark.parametrize("problem", ["cycle", "unknown_link", "bad_replacement"])
def test_invalid_supersession_never_applies_replacement(reference, problem):
    reference.requirements.version = "v2"
    record = reference.approvals.records[0]
    if problem == "cycle":
        record.supersedes = [record.id]
    elif problem == "unknown_link":
        record.supersedes = ["unknown"]
    else:
        record.replacement = "R4: otherwise -> priority_queue"
    result = prepare(reference)
    assert any(issue.code == "invalid_approval" and issue.rule == "R5" for issue in result.issues)
    assert "R5" not in {rule.id for rule in result.effective_rules}


def test_code_inspection_distinguishes_syntax_and_unsupported_without_execution(reference, tmp_path):
    marker = tmp_path / "executed"
    reference.snippets[0].source = "def decide(ticket):\n    return (\n"
    assert prepare(reference).issues[0].code == "python_syntax"
    reference.snippets[0].source = f'open({str(marker)!r}, "w")\ndef decide(ticket):\n    return True\n'
    assert prepare(reference).issues[0].code == "unsupported_python"
    assert not marker.exists()


def test_missing_code_exact_version_and_graph_cycles(reference):
    decision = reference.workflow.nodes[4]
    decision.code.version = "v9"
    decision.if_false = reference.workflow.entry
    result = prepare(reference)
    assert {issue.code for issue in result.issues} == {"workflow_cycle", "missing_snippet"}


def test_reference_routes_against_independent_oracle():
    table = json.loads((DATA / "expected" / "reference_routes.json").read_text())
    for row in table:
        assert reference_route(row) == (row["rule"], row["route"])
        if "v2_rule" in row:
            assert reference_route(row, 1500) == (row["v2_rule"], row["v2_route"])
    for case, label in LABELS["cases"].items():
        if case.startswith("d") and "witness" in label:
            witness = label["witness"]
            ticket = {**LABELS["witness_ticket_defaults"], **witness["overrides"]}
            assert reference_route(ticket, label.get("effective_wait_seconds", 1800))[1] == witness["expected_route"]


def test_frozen_dataset_and_case_balance():
    manifest = json.loads((ROOT / "evaluation" / "freeze.json").read_text())
    data_files = set()
    for split, prefix, counts in [
        ("development", "d", (6, 12, 6)),
        ("reserved", "r", (2, 4, 2)),
    ]:
        stems = {path.stem for path in (DATA / split).glob("*.json")}
        data_files.update(f"data/{split}/{stem}.json" for stem in stems)
        assert stems == {case for case in LABELS["cases"] if case.startswith(prefix)}
        assert len(stems) == manifest[split]
        assert Counter(LABELS["cases"][case]["status"] for case in stems) == dict(
            zip(("PASS", "CHANGES_REQUIRED", "INCOMPLETE"), counts, strict=True)
        )
    assert data_files <= manifest["sha256"].keys()
    assert "data/expected/cases.json" in manifest["sha256"]
    assert "evaluation/model_scenarios.json" in manifest["sha256"]
    for name, checksum in manifest["sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == checksum, name


def test_schema_exports_match_contracts():
    for name, model in [("bundle", Bundle), ("preparation", Preparation)]:
        assert json.loads((ROOT / "config" / f"{name}.schema.json").read_text()) == model.model_json_schema()


def test_cli_preparation_and_ambiguous_json(tmp_path):
    command = [sys.executable, "-m", "src.main"]
    result = subprocess.run([*command, str(REFERENCE)], cwd=ROOT, capture_output=True, check=True)
    assert Preparation.model_validate_json(result.stdout).status == "READY"
    invalid = tmp_path / "ambiguous.json"
    invalid.write_text('{"id":"first","id":"second"}')
    result = subprocess.run([*command, str(invalid)], cwd=ROOT, capture_output=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"] == "invalid_bundle"
