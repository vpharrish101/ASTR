"""Pydantic contracts for model replies, scripts, and review reports."""

from typing import Literal

from pydantic import Field

from ..stage2.contracts import Finding, Review
from ..utils.contracts import Artifact, Contract, Identifier


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


class FakeScript(Contract):
    attempt_limit: Literal[1, 2] = 2
    cases: dict[str, list[dict]]
