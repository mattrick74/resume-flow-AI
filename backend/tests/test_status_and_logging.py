from __future__ import annotations

import logging

import pytest

from resumeflow.domain.enums import PipelineStatus as S
from resumeflow.domain.status import (
    APPLICATION_TRANSITIONS,
    JOB_TRANSITIONS,
    Actor,
    InvalidTransition,
    check_application_transition,
    check_job_transition,
)
from resumeflow.logging_setup import RedactingFilter
from tests.conftest import TEST_TOKEN

HAPPY_PATH = [S.MATCHED, S.GENERATING, S.VALIDATING, S.AWAITING_REVIEW]


def test_application_happy_path():
    for current, target in zip(HAPPY_PATH, HAPPY_PATH[1:]):
        check_application_transition(current, target, Actor.SYSTEM)
    check_application_transition(S.AWAITING_REVIEW, S.APPROVED, Actor.OPERATOR)
    check_application_transition(S.APPROVED, S.EXPORTED, Actor.SYSTEM)


def test_job_path():
    check_job_transition(S.DRAFT, S.ANALYZING)
    check_job_transition(S.ANALYZING, S.MATCHED)
    check_job_transition(S.ANALYZING, S.NO_ELIGIBLE_CANDIDATE)
    with pytest.raises(InvalidTransition):
        check_job_transition(S.DRAFT, S.MATCHED)


@pytest.mark.parametrize("state", list(S))
def test_system_can_never_submit_an_application(state):
    with pytest.raises(InvalidTransition):
        check_application_transition(state, S.SUBMITTED, Actor.SYSTEM)


def test_operator_can_record_submission_only_after_export():
    check_application_transition(S.EXPORTED, S.SUBMITTED, Actor.OPERATOR)
    with pytest.raises(InvalidTransition):
        check_application_transition(S.APPROVED, S.SUBMITTED, Actor.OPERATOR)


def test_system_cannot_self_approve():
    with pytest.raises(InvalidTransition):
        check_application_transition(S.AWAITING_REVIEW, S.APPROVED, Actor.SYSTEM)


def test_every_status_is_reachable_in_some_flow():
    covered = set(JOB_TRANSITIONS) | set(APPLICATION_TRANSITIONS)
    for targets in (*JOB_TRANSITIONS.values(), *APPLICATION_TRANSITIONS.values()):
        covered |= targets
    assert covered == set(S)


def test_log_redaction():
    record = logging.LogRecord(
        "x", logging.INFO, __file__, 1,
        "auth=Bearer abc.def key=%s token=%s",
        ("sk-proj-1234567890abcdef", TEST_TOKEN),
        None,
    )
    RedactingFilter(TEST_TOKEN).filter(record)
    message = record.getMessage()
    assert "abc.def" not in message
    assert "sk-proj" not in message
    assert TEST_TOKEN not in message
