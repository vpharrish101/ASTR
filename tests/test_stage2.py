import json
from pathlib import Path

import pytest

from evaluation.checkpoint import review_samples
from evaluation.reference_oracle import reference_route
from src.stage2.check import Domain, Review, review
from src.utils.contracts import Bundle

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LABELS = json.loads((DATA / "expected" / "cases.json").read_text())["cases"]
DEVELOPMENT = sorted((DATA / "development").glob("*.json"))


def load(path):
    return Bundle.model_validate_json(path.read_bytes())


@pytest.fixture
def reference():
    return load(DATA / "development" / "d01_reference.json")


@pytest.mark.parametrize("path", DEVELOPMENT, ids=lambda path: path.stem)
def test_development_semantic_review(path):
    bundle = load(path)
    label = LABELS[bundle.id]
    report = review(bundle)
    # Three scenarios configure a later required-model check. Their routing is valid.
    assert report.status == ("PASS" if "model_failure" in label else label["status"])
    if label["status"] == "CHANGES_REQUIRED":
        anchor = label["evidence"]
        candidates = [
            item for item in report.findings
            if item.severity == "blocker" and item.rule == label["rule"] and item.witness is not None
            and any(e.artifact == anchor["artifact"] and e.version == anchor["version"]
                    and e.location == anchor["location"] for e in item.evidence)
        ]
        assert candidates
        for item in candidates:
            witness = item.witness
            assert witness.expected_route != witness.actual_route
            assert reference_route(witness.ticket, label.get("effective_wait_seconds", 1800))[1] == witness.expected_route
            assert witness.actual_route is not None or label["witness"]["actual_route"] == "NO_ROUTE"
    if issue := label.get("stage1_issue"):
        assert any(item.code == issue for item in report.preparation_issues)
        assert any(result == "unresolved" for result in report.coverage[label["rule"]].values())


def test_valid_sampling_does_not_claim_exhaustive_review(reference):
    sample = review_samples(reference)
    assert sample["samples"] > 0
    assert sample["status"] == "INCOMPLETE"
    assert review(reference).status == "PASS"


def test_missing_traceability_link_can_be_behaviorally_equivalent(reference):
    reference.spec.rules[4].source_rules = []
    report = review(reference)
    assert report.status == "PASS"
    assert report.coverage["R5"]["spec"] == "equivalent"
    assert [(item.code, item.rule) for item in report.findings] == [("missing_rule_reference", "R5")]


def test_unreachable_predicate_disagreement_is_harmless(reference):
    wait = reference.workflow.nodes[4]
    disconnected = wait.model_copy(deep=True, update={"id": "unreachable", "when": reference.workflow.nodes[2].when})
    reference.workflow.nodes.append(disconnected)
    report = review(reference)
    assert report.status == "PASS"
    assert any(item.artifact == "code:unreachable" and item.result == "equivalent" for item in report.checks)


def test_independent_dead_end_survives_source_ambiguity(reference):
    reference.requirements.text = "R1: unsupported\n"
    reference.workflow.nodes[4].if_false = None
    report = review(reference)
    assert report.status == "CHANGES_REQUIRED"
    assert report.preparation_issues
    assert any(item.code == "missing_branch" for item in report.findings)


def test_workflow_trace_does_not_blame_a_condition_that_was_bypassed(reference):
    reference.workflow.nodes[-1].route = "enterprise_queue"
    report = review(reference)
    blockers = [item for item in report.findings if item.severity == "blocker"]
    assert blockers
    assert all(item.rule == "R6" for item in blockers)


def test_solver_limit_leaves_review_incomplete(reference, monkeypatch):
    monkeypatch.setattr(Domain, "search", lambda *_: ("unresolved", None, "timeout"))
    report = review(reference)
    assert report.status == "INCOMPLETE"
    assert report.findings == []
    assert any(item.detail == "timeout" for item in report.checks)


def test_review_schema_matches_contract():
    assert json.loads((ROOT / "config" / "review.schema.json").read_text()) == Review.model_json_schema()


def test_review_recomputes_policy_after_revision_change(reference):
    assert review(reference).status == "PASS"
    reference.requirements.version = "v2"
    report = review(reference)
    assert report.status == "CHANGES_REQUIRED"
    assert report.coverage["R5"]["linked_code"] == "different"
    assert any(
        anchor.artifact == reference.requirements.id and anchor.version == "v2"
        for finding in report.findings for anchor in finding.evidence
    )


def test_review_recomputes_policy_after_approval_status_change(reference):
    assert review(reference).status == "PASS"
    proposal = next(record for record in reference.approvals.records if record.decision == "proposed")
    proposal.decision = "approved"
    report = review(reference)
    assert report.status == "CHANGES_REQUIRED"
    assert any(
        anchor.artifact == reference.approvals.id and anchor.location == f"record {proposal.id}"
        for finding in report.findings for anchor in finding.evidence
    )


def test_review_recomputes_changed_workflow_and_cites_new_version(reference):
    assert review(reference).status == "PASS"
    reference.workflow.version = "v2"
    reference.workflow.nodes[0].if_true = "priority"
    report = review(reference)
    assert report.status == "CHANGES_REQUIRED"
    assert report.coverage["R1"]["linked_code"] == "different"
    assert any(
        anchor.artifact == reference.workflow.id
        and anchor.version == "v2" and anchor.location == "/nodes/0/if_true"
        for finding in report.findings for anchor in finding.evidence
    )


def test_review_recomputes_proof_when_ticket_domain_widens(reference):
    reference.workflow.nodes[0].if_true = "priority"
    category = reference.ticket.fields["category"]
    category.nullable = False
    assert review(reference).status == "PASS"
    category.nullable = True
    report = review(reference)
    assert report.status == "CHANGES_REQUIRED"
    assert report.coverage["R1"]["linked_code"] == "different"
    assert any(
        finding.witness and finding.witness.ticket["category"] is None
        for finding in report.findings
    )
