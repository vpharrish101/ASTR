"""Pydantic contracts for deterministic checks, findings, and witnesses."""

from typing import Literal

from ..utils.contracts import Contract, Evidence, Issue


Result = Literal["equivalent", "different", "unresolved", "not_applicable"]
Ticket = dict[str, bool | int | str | None]


class Witness(Contract):
    ticket: Ticket
    expected_route: str | None
    actual_route: str | None
    expected_path: list[Evidence]
    actual_path: list[Evidence]
    expected_branch: bool | None = None
    actual_branch: bool | None = None


class Finding(Contract):
    code: str
    severity: Literal["blocker", "info"]
    rule: str | None = None
    evidence: list[Evidence]
    message: str
    correction: str
    witness: Witness | None = None


class Check(Contract):
    artifact: str
    result: Result
    rule: str | None = None
    detail: str | None = None


class Review(Contract):
    bundle: str
    status: Literal["PASS", "CHANGES_REQUIRED", "INCOMPLETE"]
    method: Literal["exact"] = "exact"
    summary: str
    findings: list[Finding]
    coverage: dict[str, dict[str, Result]]
    checks: list[Check]
    preparation_issues: list[Issue]
