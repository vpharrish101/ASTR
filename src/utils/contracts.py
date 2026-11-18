"""Versioned inputs and the output of Stage 1 preparation."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z_0-9]*$")]
Version = Annotated[str, Field(min_length=1)]
Nonnegative = Annotated[int, Field(ge=0)]
Operator = Literal["==", "<", "<=", ">", ">="]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Artifact(Contract):
    id: Identifier
    version: Version

    def anchor(self, location: str, text: str | None = None) -> "Evidence":
        return Evidence(artifact=self.id, version=self.version, location=location, text=text)

    def issue(self, code: str, message: str, location: str, *, text=None, rule=None) -> "Issue":
        return Issue(code=code, message=message, rule=rule, evidence=self.anchor(location, text))


class Evidence(Contract):
    artifact: Identifier
    version: Version
    location: str
    text: str | None = None


class Issue(Contract):
    code: str
    message: str
    evidence: Evidence
    rule: Identifier | None = None


class DurationField(Contract):
    kind: Literal["duration"]


class BooleanField(Contract):
    kind: Literal["boolean"]


class CategoryField(Contract):
    kind: Literal["category"]
    choices: Annotated[list[str], Field(min_length=1)]
    nullable: bool = False


TicketField = Annotated[DurationField | BooleanField | CategoryField, Field(discriminator="kind")]


class TicketSchema(Contract):
    fields: Annotated[dict[Identifier, TicketField], Field(min_length=1)]
    routes: Annotated[list[Identifier], Field(min_length=1)]


class Duration(Contract):
    amount: Nonnegative
    unit: Literal["sec", "min"]

    @property
    def seconds(self) -> int:
        return self.amount * (60 if self.unit == "min" else 1)


class Comparison(Contract):
    kind: Literal["comparison"] = "comparison"
    field: Identifier
    op: Operator
    value: Duration | bool | str


class Presence(Contract):
    kind: Literal["presence"] = "presence"
    field: Identifier
    present: bool


class BooleanGroup(Contract):
    kind: Literal["all", "any"]
    terms: Annotated[list["Condition"], Field(min_length=2)]


Condition = Annotated[Comparison | Presence | BooleanGroup, Field(discriminator="kind")]


class Rule(Contract):
    id: Identifier
    when: Condition | None
    route: Identifier


class Requirements(Artifact):
    text: str


class SpecRule(Rule):
    order: Annotated[int, Field(ge=1)]
    source_rules: list[Identifier] = Field(default_factory=list)


class Specification(Artifact):
    rules: list[SpecRule]


class Node(Contract):
    id: Identifier
    source_rules: list[Identifier] = Field(default_factory=list)


class Decision(Node):
    kind: Literal["decision"]
    when: Condition
    if_true: Identifier | None = None
    if_false: Identifier | None = None
    code: Artifact | None = None


class Route(Node):
    kind: Literal["route"]
    route: Identifier


class Workflow(Artifact):
    entry: Identifier
    nodes: list[Annotated[Decision | Route, Field(discriminator="kind")]]

    def dependencies(self) -> dict[str, list[str]]:
        ids = {node.id for node in self.nodes}
        return {
            node.id: [edge for edge in (node.if_true, node.if_false) if edge in ids]
            if isinstance(node, Decision) else []
            for node in self.nodes
        }


class Snippet(Artifact):
    source: str


class Approval(Contract):
    id: Identifier
    decision: Literal["approved", "proposed", "rejected"]
    target_revision: Version
    target_rule: Identifier
    replacement: str
    supersedes: list[Identifier] = Field(default_factory=list)


class ApprovalLog(Artifact):
    records: list[Approval]


class Bundle(Contract):
    id: Identifier
    ticket: TicketSchema
    requirements: Requirements
    spec: Specification
    workflow: Workflow
    snippets: list[Snippet]
    approvals: ApprovalLog

    @model_validator(mode="after")
    def unique_keys(self):
        artifacts = [self.requirements, self.spec, self.workflow, self.approvals, *self.snippets]
        groups = {
            "ticket.routes": self.ticket.routes,
            "artifact ID/version pairs": [(artifact.id, artifact.version) for artifact in artifacts],
            "spec rule IDs": [rule.id for rule in self.spec.rules],
            "workflow node IDs": [node.id for node in self.workflow.nodes],
            "approval IDs": [record.id for record in self.approvals.records],
        }
        for label, keys in groups.items():
            if len(keys) != len(set(keys)):
                raise ValueError(f"duplicate {label}")
        for name, field in self.ticket.fields.items():
            if isinstance(field, CategoryField) and len(field.choices) != len(set(field.choices)):
                raise ValueError(f"duplicate category choices for {name}")
        return self


class EffectiveRule(Rule):
    source: Evidence
    approval: Evidence | None = None


class ParsedSnippet(Contract):
    condition: Condition
    evidence: Evidence


class Preparation(Contract):
    bundle: Identifier
    status: Literal["READY", "UNRESOLVED"]
    source_revision: Version
    effective_rules: list[EffectiveRule]
    snippets: list[ParsedSnippet]
    issues: list[Issue]
