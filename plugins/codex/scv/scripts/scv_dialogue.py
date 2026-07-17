#!/usr/bin/env python3
"""Presentation-only Korean labels and SCV lines for CLI output."""

from __future__ import annotations

from enum import Enum
from typing import Any


SCV_STATE_LINES = {
    "NEW": "Reportin' for duty.",
    "INTAKING": "I read you.",
    "AWAITING_SPEC_APPROVAL": "Orders, Cap'n?",
    "PLANNING": "SCV good to go, sir.",
    "AWAITING_PLAN_APPROVAL": "Yes sir?",
    "BASE_REVALIDATION": "Affirmative.",
    "MATERIALIZING_WORKTREE": "Right away sir.",
    "EXECUTING": "Orders received.",
    "HANDOFF": "Roger that.",
    "READY": "Job's finished.",
    "BLOCKED": "I can't build there.",
    "ABANDONED": "I'm not readin' you clearly.",
}

SCV_STATE_LABELS = {
    "NEW": "태스크 초기화",
    "INTAKING": "요구사항 접수",
    "AWAITING_SPEC_APPROVAL": "스펙 승인 대기",
    "PLANNING": "구현 계획 작성",
    "AWAITING_PLAN_APPROVAL": "계획 승인 대기",
    "BASE_REVALIDATION": "승인 기준 재확인",
    "MATERIALIZING_WORKTREE": "격리 워크트리 생성",
    "EXECUTING": "실행 중",
    "HANDOFF": "검증 결과 인계",
    "READY": "인계 준비 완료",
    "BLOCKED": "차단됨",
    "ABANDONED": "포기됨",
}

SCV_PROGRESS_LINES = {
    "pending": SCV_STATE_LINES["NEW"],
    "running": SCV_STATE_LINES["EXECUTING"],
    "passed": SCV_STATE_LINES["READY"],
    "ready": SCV_STATE_LINES["READY"],
    "blocked": SCV_STATE_LINES["BLOCKED"],
    "failed": SCV_STATE_LINES["BLOCKED"],
    "timed_out": SCV_STATE_LINES["BLOCKED"],
    "unavailable": SCV_STATE_LINES["BLOCKED"],
}

SCV_STAGE_LINES = {
    "starting": SCV_STATE_LINES["NEW"],
    "worker": SCV_STATE_LINES["EXECUTING"],
    "acceptance": SCV_STATE_LINES["BASE_REVALIDATION"],
    "verifier": SCV_STATE_LINES["INTAKING"],
    "failure-analysis": "Come again, Cap'n?",
    "retry": SCV_STATE_LINES["PLANNING"],
    "step-complete": SCV_STATE_LINES["READY"],
    "final-acceptance": SCV_STATE_LINES["BASE_REVALIDATION"],
    "final-verifier": SCV_STATE_LINES["INTAKING"],
    "complete": SCV_STATE_LINES["READY"],
    "blocked": SCV_STATE_LINES["BLOCKED"],
    "failed": SCV_STATE_LINES["BLOCKED"],
    "cancelled": SCV_STATE_LINES["ABANDONED"],
}

SCV_STAGE_LABELS = {
    "starting": "실행 환경 준비",
    "worker": "단계 구현",
    "acceptance": "단계 인수 검사",
    "verifier": "단계 읽기 전용 검증",
    "failure-analysis": "실패 증거 분석",
    "retry": "재시도 준비",
    "step-complete": "단계 완료",
    "final-acceptance": "전체 인수 검사",
    "final-verifier": "전체 읽기 전용 검증",
    "complete": "실행 완료",
    "blocked": "차단됨",
    "failed": "실패",
    "cancelled": "취소됨",
}


def _string_value(value: object) -> str | None:
    if isinstance(value, Enum):
        value = value.value
    return value if isinstance(value, str) else None


def scv_line_for_state(state: object) -> str | None:
    """Return the voice line for a durable lifecycle state."""

    value = _string_value(state)
    return SCV_STATE_LINES.get(value) if value is not None else None


def scv_label_for_state(state: object) -> str | None:
    """Return the Korean presentation label for a durable state."""

    value = _string_value(state)
    return SCV_STATE_LABELS.get(value) if value is not None else None


def scv_line_for_progress(status: object) -> str | None:
    """Return the voice line for an executor progress status."""

    value = _string_value(status)
    return SCV_PROGRESS_LINES.get(value) if value is not None else None


def scv_line_for_stage(stage: object) -> str | None:
    """Return the voice line for a public execution stage."""

    value = _string_value(stage)
    return SCV_STAGE_LINES.get(value) if value is not None else None


def scv_label_for_stage(stage: object) -> str | None:
    """Return the Korean presentation label for an execution stage."""

    value = _string_value(stage)
    return SCV_STAGE_LABELS.get(value) if value is not None else None


def decorate_scv_output(value: Any) -> Any:
    """Add computed presentation fields without mutating persisted data."""

    if isinstance(value, list):
        return [decorate_scv_output(item) for item in value]
    if not isinstance(value, dict):
        return value
    rendered = dict(value)
    state_label = scv_label_for_state(rendered.get("state"))
    if state_label is not None:
        rendered["state_label"] = state_label
    stage_label = scv_label_for_stage(rendered.get("stage"))
    if stage_label is not None:
        rendered["stage_label"] = stage_label
    line = scv_line_for_state(rendered.get("state"))
    if line is None:
        line = scv_line_for_stage(rendered.get("stage"))
    if line is None:
        line = scv_line_for_progress(rendered.get("status"))
    if line is not None:
        rendered["scv_line"] = line
    return rendered
