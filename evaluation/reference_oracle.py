"""Independent routing oracle for the hand-authored policy and its wait revisions."""


def reference_route(ticket, escalation_seconds=1800):
    if ticket["category"] is None:
        return "R1", "manual_review"
    if ticket["fraud"]:
        return "R2", "manual_review"
    if ticket["urgent"]:
        return "R3", "priority_queue"
    if ticket["tier"] == "enterprise" and ticket["category"] in {"billing", "technical"}:
        return "R4", "enterprise_queue"
    if ticket["waiting"] >= escalation_seconds:
        return "R5", "escalation"
    return "R6", "normal_queue"
