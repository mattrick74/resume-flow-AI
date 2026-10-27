"""Allowed status transitions for jobs and applications (PRD section 14).

The key invariant: generating or exporting files never moves an application to
SUBMITTED. Post-export outcomes are recorded only by an explicit operator action.
"""

from __future__ import annotations

from enum import StrEnum

from resumeflow.domain.enums import PipelineStatus as S


class Actor(StrEnum):
    SYSTEM = "system"
    OPERATOR = "operator"
    # A rule the operator switched on in Settings ("export automatically"). It may approve, never submit.
    AUTOMATION = "automation"


JOB_TRANSITIONS: dict[S, frozenset[S]] = {
    S.DRAFT: frozenset({S.ANALYZING}),
    S.ANALYZING: frozenset({S.MATCHED, S.NO_ELIGIBLE_CANDIDATE, S.FAILED}),
    S.MATCHED: frozenset({S.ANALYZING}),
    S.NO_ELIGIBLE_CANDIDATE: frozenset({S.ANALYZING}),
    S.FAILED: frozenset({S.ANALYZING}),
}

APPLICATION_TRANSITIONS: dict[S, frozenset[S]] = {
    S.MATCHED: frozenset({S.GENERATING, S.FAILED}),
    # Documents go straight from writing to the next step (no checking step); VALIDATING stays for older data.
    S.GENERATING: frozenset({S.AWAITING_REVIEW, S.VALIDATING, S.FAILED}),
    S.VALIDATING: frozenset({S.AWAITING_REVIEW, S.FAILED}),
    S.AWAITING_REVIEW: frozenset({S.APPROVED, S.GENERATING}),
    # Editing approved/exported content sends it back for review; it is never silently changed.
    S.APPROVED: frozenset({S.EXPORTED, S.GENERATING, S.AWAITING_REVIEW}),
    S.EXPORTED: frozenset({S.SUBMITTED, S.GENERATING, S.AWAITING_REVIEW}),
    # v2.0: deleting the captured confirmation screen undoes "Submitted".
    S.SUBMITTED: frozenset({S.INTERVIEW, S.REJECTED, S.OFFER, S.EXPORTED}),
    S.INTERVIEW: frozenset({S.OFFER, S.REJECTED}),
    S.OFFER: frozenset(),
    S.REJECTED: frozenset(),
    S.FAILED: frozenset({S.GENERATING, S.VALIDATING}),
}

# States only an operator may move an application into.
OPERATOR_ONLY: frozenset[S] = frozenset({S.APPROVED, S.SUBMITTED, S.INTERVIEW, S.REJECTED, S.OFFER})


class InvalidTransition(ValueError):
    pass


def check_application_transition(current: S, target: S, actor: Actor) -> None:
    allowed = APPLICATION_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidTransition(f"Application cannot move from {current} to {target}")
    approved_by_rule = target == S.APPROVED and actor is Actor.AUTOMATION
    if target in OPERATOR_ONLY and actor is not Actor.OPERATOR and not approved_by_rule:
        raise InvalidTransition(f"{target} requires an explicit operator action")


def check_job_transition(current: S, target: S) -> None:
    if target not in JOB_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition(f"Job cannot move from {current} to {target}")
