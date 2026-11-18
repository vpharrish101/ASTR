"""Z3 encoding and search for the declared ticket domain."""

from collections import defaultdict
from graphlib import TopologicalSorter

import z3

from ..utils.contracts import BooleanField, BooleanGroup, CategoryField, Duration, Presence, Route
from .replay import OPERATORS

NO_ROUTE, UNKNOWN = 0, -1


class Domain:
    def __init__(self, ticket_schema, timeout_ms):
        self.solver = z3.Solver()
        self.solver.set(timeout=timeout_ms)
        self.variables = {}
        self.categories = {}
        self.routes = {name: index for index, name in enumerate(ticket_schema.routes, 1)}
        for name, field in ticket_schema.fields.items():
            if isinstance(field, BooleanField):
                self.variables[name] = z3.Bool(name)
            else:
                variable = self.variables[name] = z3.Int(name)
                self.solver.add(variable >= 0)
                if isinstance(field, CategoryField):
                    options = self.categories[name] = field.choices + ([None] if field.nullable else [])
                    self.solver.add(variable < len(options))

    def predicate(self, condition):
        if isinstance(condition, BooleanGroup):
            combine = z3.And if condition.kind == "all" else z3.Or
            return combine(*(self.predicate(term) for term in condition.terms))
        variable = self.variables[condition.field]
        categories = self.categories.get(condition.field, [])
        if isinstance(condition, Presence):
            present = variable != categories.index(None) if None in categories else z3.BoolVal(True)
            return present if condition.present else z3.Not(present)
        value = condition.value
        if isinstance(value, Duration):
            value = value.seconds
        elif categories:
            value = categories.index(value)
        return OPERATORS[condition.op](variable, value)

    def search(self, query):
        result = self.solver.check(query)
        if result == z3.unknown:
            return "unresolved", None, self.solver.reason_unknown()
        if result == z3.unsat:
            return "equivalent", None, None
        model = self.solver.model()
        ticket = {}
        for name, variable in self.variables.items():
            value = model.eval(variable, model_completion=True)
            if z3.is_bool(variable):
                ticket[name] = z3.is_true(value)
            else:
                number = value.as_long()
                ticket[name] = self.categories[name][number] if name in self.categories else number
        return "different", ticket, None

    def policy(self, rules):
        remaining = z3.BoolVal(True)
        regions = []
        outcome = z3.IntVal(NO_ROUTE)
        for condition, route, _, _ in rules:
            guard = self.predicate(condition) if condition is not None else z3.BoolVal(True)
            regions.append(z3.And(remaining, guard))
            outcome = z3.If(regions[-1], self.routes[route], outcome)
            remaining = z3.And(remaining, z3.Not(guard))
        return outcome, regions

    def workflow(self, paths, linked_code=False):
        order = reversed(list(TopologicalSorter(paths.workflow.dependencies()).static_order()))
        reach = defaultdict(lambda: z3.BoolVal(False))
        reach[paths.workflow.entry] = z3.BoolVal(True)
        outcome = z3.IntVal(NO_ROUTE)
        for key in order:
            if key not in reach:
                continue
            _, node = paths.nodes[key]
            if isinstance(node, Route):
                outcome = z3.If(reach[key], self.routes[node.route], outcome)
                continue
            condition = paths.guard(node, linked_code)
            if condition is None:
                outcome = z3.If(reach[key], UNKNOWN, outcome)
                continue
            guard = self.predicate(condition)
            for target, branch in ((node.if_true, guard), (node.if_false, z3.Not(guard))):
                if target in paths.nodes:
                    reach[target] = z3.Or(reach[target], z3.And(reach[key], branch))
        return outcome, reach
