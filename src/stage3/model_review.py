"""Bounded, advisory model review over a freshly prepared deterministic report."""

import json
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import ValidationError

from ..stage2.check import review_with_preparation, verdict
from ..stage2.contracts import Check, Finding
from ..utils.contracts import Bundle, decode_json, read_bounded
from .clients import FakeClient, ModelClient, ModelClientError
from .clients import OllamaClient as OllamaClient
from .contracts import AssistedReview, Attempt, CodeReview, FakeScript, ModelResponse, ModelRun
from .contracts import ModelRow as ModelRow

PROMPT = Path(__file__).resolve().parents[2] / "config" / "reviewer_prompt.md"
BUNDLE_BYTES, REQUEST_BYTES, RESPONSE_BYTES = 1_048_576, 65_536, 16_384
DEADLINE_SECONDS = 10.0
FOLLOW_UP = {
    "clarify_requirement": "Clarify the requirement with the policy owner.",
    "clarify_precedence": "Confirm this rule's precedence with the policy owner.",
    "clarify_approval": "Confirm the effective revision and approval wording with the policy owner.",
}


def validate_response(raw, request_id, rules):
    try:
        payload = decode_json(raw)
    except (ValueError, RecursionError):
        return "invalid_json", []
    try:
        result = ModelResponse.model_validate(payload)
    except ValidationError:
        return "invalid_report", []
    if result.request_id != request_id:
        return "stale_response", []
    seen = set()
    for row in result.rows:
        if row.rule not in rules:
            return "invented_reference", []
        evidence_id, evidence, expected_route = rules[row.rule]
        if row.evidence_id != evidence_id:
            return "invented_reference", []
        if row.rule in seen:
            return "invalid_coverage", []
        if row.quote != evidence.text:
            return "quote_mismatch", []
        if row.expected_route != expected_route:
            return "conflicting_claim", []
        seen.add(row.rule)
    return ("accepted", result.rows) if seen == rules.keys() else ("invalid_coverage", [])


def run_model(bundle_bytes, report, prepared, client, attempt_limit, error):
    settings = getattr(client, "settings", {})
    time_budget = settings.get("deadline_seconds", DEADLINE_SECONDS)
    run = ModelRun(mode="required", result="unresolved", client=client.name if client else None,
                   coverage=dict.fromkeys(report.coverage, "unreviewed"),
                   limits={**settings, "attempts": attempt_limit, "deadline_seconds": time_budget,
                           "bundle_bytes": BUNDLE_BYTES, "request_bytes": REQUEST_BYTES,
                           "response_bytes": RESPONSE_BYTES})
    if error or client is None or report.preparation_issues or any(c.result == "unresolved" for c in report.checks):
        run.reason = error or ("client_unavailable" if client is None else "deterministic_scope_unresolved")
        return run
    try:
        prompt_bytes = read_bounded(PROMPT, REQUEST_BYTES)
        prompt = prompt_bytes.decode()
    except (OSError, ValueError):
        run.reason = "prompt_unavailable"
        return run
    rules = {rule.id: (f"E{index}", rule.approval or rule.source, rule.route)
             for index, rule in enumerate(prepared.effective_rules, 1)}
    run.prompt_sha256 = sha256(prompt_bytes).hexdigest()
    payload = {
        "bundle_sha256": sha256(bundle_bytes).hexdigest(), "prompt_sha256": run.prompt_sha256,
        "client": client.name, "limits": run.limits,
        "rules": [{"rule": rule.id, "evidence_id": rules[rule.id][0], "source": rule.source.model_dump(),
                   "effective": rules[rule.id][1].model_dump()} for rule in prepared.effective_rules],
        "findings": [finding.model_dump() for finding in report.findings],
        "response_schema": ModelResponse.model_json_schema(),
    }
    run.request_id = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    request = json.dumps({**payload, "request_id": run.request_id}).encode()
    run.request_bytes = len(prompt_bytes) + len(request)
    if run.request_bytes > REQUEST_BYTES:
        run.reason = "request_too_large"
        return run

    deadline = monotonic() + time_budget
    for _ in range(attempt_limit):
        start = monotonic()
        if start >= deadline:
            run.reason = "timeout"
            break
        size = digest = None
        rows = []
        try:
            raw = client.complete(prompt, request, timeout=deadline - start)
            size = len(raw)
            if size > RESPONSE_BYTES:
                outcome = "response_too_large"
            else:
                digest = sha256(raw).hexdigest()
                outcome, rows = validate_response(raw, run.request_id, rules)
        except TimeoutError:
            outcome = "timeout"
        except ModelClientError as exc:
            outcome = str(exc)
        except (OSError, ValueError, TypeError, KeyError, StopIteration):
            outcome = "client_error"
        end = monotonic()
        if end >= deadline:
            outcome = "timeout"
        run.attempts.append(Attempt(result=outcome, response_bytes=size, response_sha256=digest,
                                    elapsed_ms=round((end - start) * 1000, 3)))
        if outcome == "accepted":
            run.result, run.reason = "accepted", None
            run.coverage = dict.fromkeys(rules, "reviewed")
            run.advice = [Finding(code=row.follow_up, severity="info", rule=row.rule,
                                  message="Model suggested a human follow-up.", correction=FOLLOW_UP[row.follow_up],
                                  evidence=[rules[row.rule][1]]) for row in rows if row.follow_up != "none"]
            break
        run.reason = outcome
    return run


def unreviewed(bundle_name, detail, *, required):
    return AssistedReview(
        bundle=bundle_name, status="INCOMPLETE", summary="The bundle remains unreviewed.",
        findings=[], coverage={}, checks=[Check(artifact="bundle", result="unresolved", detail=detail)],
        preparation_issues=[], code_review=[],
        model_review=ModelRun(mode="required" if required else "offline",
                              result="unresolved" if required else "not_requested", reason="bundle_unreviewed"),
    )


def assisted_review(bundle: Bundle, client: ModelClient | None = None, *,
                    script_path: Path | None = None, attempt_limit: Literal[1, 2] = 2,
                    compare_routes: bool = True) -> AssistedReview:
    required = client is not None or script_path is not None
    bundle_bytes = bundle.model_dump_json().encode()
    if len(bundle_bytes) > BUNDLE_BYTES:
        return unreviewed(bundle.id, f"Input exceeds {BUNDLE_BYTES} bytes.", required=required)
    try:
        report, prepared = review_with_preparation(bundle, compare_routes=compare_routes)
    except RecursionError:
        return unreviewed(bundle.id, "The bundle exceeded the review nesting limit.", required=required)
    error = None
    if script_path is not None:
        try:
            script = FakeScript.model_validate(decode_json(read_bounded(script_path, BUNDLE_BYTES)))
            client, attempt_limit = FakeClient(script.cases.get(bundle.id)), script.attempt_limit
        except (OSError, ValueError, RecursionError):
            error = "invalid_fake_script"
    model = (run_model(bundle_bytes, report, prepared, client, min(2, max(1, attempt_limit)), error) if required
             else ModelRun(mode="offline", result="not_requested"))

    code_issues = {(issue.evidence.artifact, issue.evidence.version): issue.code for issue in prepared.issues
                   if issue.code in {"python_syntax", "unsupported_python"}}
    code_review = []
    for snippet in bundle.snippets:
        issue = code_issues.get((snippet.id, snippet.version))
        code_review.append(CodeReview(id=snippet.id, version=snippet.version,
                                      syntax="invalid" if issue == "python_syntax" else "parsed",
                                      inspection={"python_syntax": "not_run", "unsupported_python": "unsupported"}
                                      .get(issue, "supported")))
    data = report.model_dump()
    if model.result == "unresolved":
        check = Check(artifact="model", result="unresolved", detail=model.reason)
        checks = [*report.checks, check]
        status, summary = verdict(report.findings, checks, report.preparation_issues)
        data.update(status=status, summary=summary, checks=[item.model_dump() for item in checks])
    return AssistedReview(**data, model_review=model, code_review=code_review)
