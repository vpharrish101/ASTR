"""Parse anchored source rules and resolve explicitly scoped approvals."""

import json
import re
from collections import Counter, defaultdict
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

from lark import Lark, Transformer, UnexpectedInput, v_args
from lark.exceptions import VisitError

from ..utils.contracts import (
    BooleanField, BooleanGroup, Bundle, CategoryField, Comparison, Condition, Duration,
    DurationField, EffectiveRule, Evidence, Issue, Presence, Rule, TicketSchema,
)

GRAMMAR = Path(__file__).resolve().parents[2] / "config" / "requirements.lark"
PARSER = Lark.open(str(GRAMMAR), parser="lalr")
RULE_PREFIX = re.compile(r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s*:")


@v_args(inline=True)
class RuleTransformer(Transformer):
    def conditional(self, key, condition, route):
        return Rule(id=str(key), when=condition, route=str(route))

    def fallback(self, key, route):
        return Rule(id=str(key), when=None, route=str(route))

    def any_of(self, *terms):
        return BooleanGroup(kind="any", terms=list(terms))

    def all_of(self, *terms):
        return BooleanGroup(kind="all", terms=list(terms))

    def compare(self, field, op, value):
        return Comparison(field=str(field), op=str(op), value=value)

    def missing(self, field):
        return Presence(field=str(field), present=False)

    def present(self, field):
        return Presence(field=str(field), present=True)

    def duration(self, amount, unit):
        return Duration(amount=int(amount), unit=str(unit))

    def category(self, value):
        return json.loads(value)

    def true_value(self):
        return True

    def false_value(self):
        return False


def parse_rule(text: str) -> Rule:
    return RuleTransformer().transform(PARSER.parse(text))


def condition_error(condition: Condition, ticket: TicketSchema) -> str | None:
    if isinstance(condition, BooleanGroup):
        for term in condition.terms:
            if error := condition_error(term, ticket):
                return error
        return None

    field = ticket.fields.get(condition.field)
    if field is None:
        return f"Unknown ticket field {condition.field!r}"
    if isinstance(condition, Presence):
        return None
    value = condition.value
    if isinstance(field, DurationField) and isinstance(value, Duration):
        return None
    if condition.op != "==":
        return f"{condition.op} requires a duration field and duration literal"
    if isinstance(field, BooleanField) and type(value) is bool:
        return None
    if isinstance(field, CategoryField) and isinstance(value, str) and value in field.choices:
        return None
    return f"Value is incompatible with ticket field {condition.field!r}"


def rule_error(rule: Rule, ticket: TicketSchema) -> str | None:
    if rule.route not in ticket.routes:
        return f"Unknown route {rule.route!r}"
    if rule.when is not None:
        return condition_error(rule.when, ticket)
    return None


def resolve_policy(bundle: Bundle) -> tuple[list[EffectiveRule], set[str], list[Issue]]:
    source = bundle.requirements
    log = bundle.approvals
    entries = [(number, text) for number, text in enumerate(source.text.splitlines(), 1) if text.strip()]
    ids = Counter(match[1] for _, text in entries if (match := RULE_PREFIX.match(text)))
    originals: dict[str, EffectiveRule] = {}
    issues: list[Issue] = []
    for number, text in entries:
        prefix = RULE_PREFIX.match(text)
        rule_id = prefix[1] if prefix else None
        evidence = Evidence(
            artifact=source.id, version=source.version,
            location=f"line {number}:{len(text) - len(text.lstrip()) + 1}", text=text,
        )
        error_code = "unsupported_source"
        try:
            rule = parse_rule(text)
        except UnexpectedInput as exc:
            evidence.location = f"line {number}:{max(1, exc.column)}"
            error = "Expected one complete rule in the documented DSL"
        except VisitError:
            error = "Invalid DSL literal"
        else:
            error = rule_error(rule, bundle.ticket)
            if ids[rule.id] > 1:
                error_code = "duplicate_source_rule"
                error = "The source rule ID occurs on multiple lines"
            if error is None:
                originals[rule.id] = EffectiveRule(**rule.model_dump(), source=evidence)
        if error is not None:
            issues.append(Issue(code=error_code, message=error, evidence=evidence, rule=rule_id))

    active = defaultdict(dict)
    for record in log.records:
        if record.decision == "approved" and record.target_revision == source.version:
            active[record.target_rule][record.id] = record

    unresolved = set()
    for target, records in active.items():
        links = {key: set(record.supersedes) for key, record in records.items()}
        replaced = set().union(*links.values())
        winners = records.keys() - replaced
        error_code = "invalid_approval"
        error = None
        if target not in originals:
            error = "Approval does not identify a unique supported rule in this revision"
        elif replaced - records.keys():
            error = "Supersession must link to an applicable approved record for the same rule"
        else:
            try:
                tuple(TopologicalSorter(links).static_order())
            except CycleError:
                error = "Approval supersession contains a cycle"
            if error is None and len(winners) != 1:
                error_code = "approval_conflict"
                error = "Applicable approvals have no unique unsuperseded record"

        if error is None:
            winner = records[next(iter(winners))]
            try:
                replacement = parse_rule(winner.replacement)
            except (UnexpectedInput, VisitError):
                error = "The replacement must be a single complete DSL rule"
            else:
                error = rule_error(replacement, bundle.ticket)
                if replacement.id != target:
                    error = "Replacement must keep the target rule ID"
                elif (replacement.when is None) != (originals[target].when is None):
                    error = "Replacement must preserve whether the rule is the fallback"

        if error is not None:
            unresolved.add(target)
            for key, record in records.items():
                issues.append(Issue(
                    code=error_code, message=error, rule=target,
                    evidence=Evidence(
                        artifact=log.id, version=log.version, location=f"record {key}",
                        text=record.replacement,
                    ),
                ))
        else:
            originals[target] = EffectiveRule(
                **replacement.model_dump(), source=originals[target].source,
                approval=Evidence(
                    artifact=log.id, version=log.version,
                    location=f"record {winner.id}", text=winner.replacement,
                ),
            )

    effective = [rule for key, rule in originals.items() if key not in unresolved]
    fallbacks = [rule for rule in effective if rule.when is None]
    if len(fallbacks) != 1 or fallbacks[0] is not effective[-1]:
        issues.append(Issue(
            code="invalid_fallback", message="The effective policy needs one final otherwise rule",
            evidence=Evidence(artifact=source.id, version=source.version, location="policy"),
        ))
    return effective, set(ids), issues
