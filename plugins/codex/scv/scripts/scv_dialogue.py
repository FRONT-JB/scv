#!/usr/bin/env python3
"""Presentation-only SCV voice lines for lifecycle and execution output."""

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


def _string_value(value: object) -> str | None:
    if isinstance(value, Enum):
        value = value.value
    return value if isinstance(value, str) else None


def scv_line_for_state(state: object) -> str | None:
    """Return the voice line for a durable lifecycle state."""

    value = _string_value(state)
    return SCV_STATE_LINES.get(value) if value is not None else None


def scv_line_for_progress(status: object) -> str | None:
    """Return the voice line for an executor progress status."""

    value = _string_value(status)
    return SCV_PROGRESS_LINES.get(value) if value is not None else None


def scv_line_for_stage(stage: object) -> str | None:
    """Return the voice line for a public execution stage."""

    value = _string_value(stage)
    return SCV_STAGE_LINES.get(value) if value is not None else None


def decorate_scv_output(value: Any) -> Any:
    """Add a computed ``scv_line`` without mutating persisted data."""

    if isinstance(value, list):
        return [decorate_scv_output(item) for item in value]
    if not isinstance(value, dict):
        return value
    rendered = dict(value)
    line = scv_line_for_state(rendered.get("state"))
    if line is None:
        line = scv_line_for_stage(rendered.get("stage"))
    if line is None:
        line = scv_line_for_progress(rendered.get("status"))
    if line is not None:
        rendered["scv_line"] = line
    return rendered
