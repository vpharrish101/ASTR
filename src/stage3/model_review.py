"""Bounded, advisory model review over a freshly prepared deterministic report."""

import json
import subprocess
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Literal, Protocol

from pydantic import Field, ValidationError

from ..stage2.check import Check, Finding, Review, review_with_preparation, verdict
from ..utils.contracts import Artifact, Bundle, Contract, Identifier, decode_json, read_bounded

PROMPT = Path(__file__).resolve().parents[2] / "config" / "reviewer_prompt.md"
BUNDLE_BYTES, REQUEST_BYTES, RESPONSE_BYTES = 1_048_576, 65_536, 16_384
DEADLINE_SECONDS = 10.0
FOLLOW_UP = {
    "clarify_requirement": "Clarify the requirement with the policy owner.",
    "clarify_precedence": "Confirm this rule's precedence with the policy owner.",
    "clarify_approval": "Confirm the effective revision and approval wording with the policy owner.",
}


class ModelRow(Contract):
    rule: Identifier
    expected_route: Identifier
    evidence_id: str
    quote: str
    follow_up: Literal["none", "clarify_requirement", "clarify_precedence", "clarify_approval"]


class ModelResponse(Contract):
    request_id: str
    rows: list[ModelRow]


class Attempt(Contract):
    result: str
    response_bytes: int | None = None
    response_sha256: str | None = None
    elapsed_ms: float


class ModelRun(Contract):
    mode: Literal["offline", "required"]
    result: Literal["not_requested", "accepted", "unresolved"]
    reason: str | None = None
    client: str | None = None
    prompt_sha256: str | None = None
    request_id: str | None = None
    request_bytes: int | None = None
    limits: dict[str, int | float] = Field(default_factory=dict)
    attempts: list[Attempt] = Field(default_factory=list)
    coverage: dict[str, Literal["reviewed", "unreviewed"]] = Field(default_factory=dict)
    advice: list[Finding] = Field(default_factory=list)


class CodeReview(Artifact):
    syntax: Literal["parsed", "invalid"]
    inspection: Literal["supported", "unsupported", "not_run"]
    execution: Literal["not_run"] = "not_run"


class AssistedReview(Review):
    model_review: ModelRun
    code_review: list[CodeReview]


class ModelClient(Protocol):
    name: str

    def complete(self, prompt: str, request: bytes, *, timeout: float) -> bytes:
        """Return bounded response bytes. Enforce timeout at the transport."""


class ModelClientError(Exception):
    """A transport or provider failure with a reportable reason."""


class OllamaClient:
    url = "http://127.0.0.1:11434/api/chat"
    options = {"num_ctx": 8192, "num_predict": 2048, "num_gpu": 0, "temperature": 0, "seed": 42}
    settings = {**options, "deadline_seconds": 240.0, "http_response_bytes": 65_536,
                "think": 0, "truncate": 0, "shift": 0}

    def __init__(self, model: str = "qwen3:4b"):
        self.model = model
        self.name = f"ollama:{model}"

    def complete(self, prompt: str, request: bytes, *, timeout: float) -> bytes:
        payload = {
            "model": self.model, "stream": False, "think": False, "truncate": False, "shift": False,
            "format": decode_json(request)["response_schema"], "options": self.options,
            "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": request.decode()}],
        }
        command = [
            "curl", "--disable", "--silent", "--fail", "--noproxy", "*",
            "--max-time", f"{timeout:.3f}", "--max-filesize", str(self.settings["http_response_bytes"]),
            "--write-out", "\n%{http_code}", "--json", "@-", self.url,
        ]
        try:
            result = subprocess.run(command, input=json.dumps(payload).encode(), capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError from exc
        except FileNotFoundError as exc:
            raise ModelClientError("curl_unavailable") from exc

        body, _, http_status = result.stdout.rpartition(b"\n")
        if result.returncode == 28:
            raise TimeoutError
        if result.returncode:
            reason = {7: "ollama_unavailable", 22: "ollama_http_error", 63: "response_too_large"}
            raise ModelClientError("model_context_limit" if http_status == b"413"
                                   else reason.get(result.returncode, "ollama_transport_error"))
        if http_status != b"200":
            raise ModelClientError("ollama_http_error")
        if len(body) > self.settings["http_response_bytes"]:
            raise ModelClientError("response_too_large")
        try:
            response = decode_json(body)
            content = response["message"]["content"]
            if response.get("model") != self.model or response.get("remote_model") or response.get("remote_host"):
                raise ModelClientError("unexpected_model")
            if response.get("done") is not True or response.get("done_reason") != "stop":
                raise ModelClientError("truncated_response")
            if not isinstance(content, str):
                raise TypeError("Expected string content")
            return content.encode()
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise ModelClientError("invalid_ollama_response") from exc


class FakeScript(Contract):
    attempt_limit: Literal[1, 2] = 2
    cases: dict[str, list[dict]]


class FakeClient:
    name = "fake"

    def __init__(self, events: Iterable[dict] | None = None):
        self.events = None if events is None else iter(events)

    def complete(self, prompt: str, request: bytes, *, timeout: float) -> bytes:
        event = {"event": "valid"} if self.events is None else next(self.events, {"event": "exhausted"})
        kind = event["event"]
        if kind == "timeout":
            raise TimeoutError
        if kind == "raw_response":
            return event["body"].encode()
        if kind not in {"valid", "invented_citation", "contradictory_claim"}:
            raise ValueError("Unavailable fake response")
        request = decode_json(request)
        rows = [{
            "rule": item["rule"], "evidence_id": item["evidence_id"], "quote": item["effective"]["text"],
            "expected_route": item["effective"]["text"].rsplit("->", 1)[-1].strip(),
            "follow_up": event.get("follow_up", "none"),
        } for item in request["rules"]]
        if kind == "invented_citation":
            rows[0].update(rule=event["rule"], evidence_id=f'{event["artifact"]}@{event["version"]}:{event["location"]}')
        if kind == "contradictory_claim":
            row = next(item for item in rows if item["rule"] == event["rule"])
            row["expected_route"] = event["claim"].rstrip(".").split()[-1]
        return json.dumps({"request_id": request["request_id"], "rows": rows}).encode()


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
                    script_path: Path | None = None, attempt_limit: Literal[1, 2] = 2) -> AssistedReview:
    required = client is not None or script_path is not None
    bundle_bytes = bundle.model_dump_json().encode()
    if len(bundle_bytes) > BUNDLE_BYTES:
        return unreviewed(bundle.id, f"Input exceeds {BUNDLE_BYTES} bytes.", required=required)
    try:
        report, prepared = review_with_preparation(bundle)
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
