#!/usr/bin/env python3
"""Persistent control-plane state for the Codex SCV workflow.

The state store deliberately has no dependency on external user-level skills.
Task state lives below Git's common directory so linked worktrees see one
authoritative record without adding workflow data to the repository checkout.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, MutableMapping, Optional, Union

try:
    import fcntl
except ImportError:  # pragma: no cover - macOS 전용 오류를 먼저 표시합니다.
    fcntl = None  # type: ignore[assignment]

try:
    from .cli_ko import localize_argparse
    from .runtime import RuntimeRequirementError, require_macos
    from .scv_dialogue import decorate_scv_output
except ImportError:  # pragma: no cover - direct script execution.
    from cli_ko import localize_argparse
    from runtime import RuntimeRequirementError, require_macos
    from scv_dialogue import decorate_scv_output


SCHEMA_VERSION = 1
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ARTIFACT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
TARGETS = frozenset({"analyze", "plan", "full"})


class SCVStateError(RuntimeError):
    """Base error for state discovery, validation, and persistence failures."""


class InvalidTaskId(SCVStateError, ValueError):
    """Raised when a task ID is unsafe to use as a directory name."""


class InvalidTransition(SCVStateError, ValueError):
    """Raised when a requested lifecycle transition is not legal."""


class TaskExists(SCVStateError):
    """Raised when creation would replace an existing task."""


class TaskNotFound(SCVStateError):
    """Raised when a requested task has no state record."""


class CorruptState(SCVStateError):
    """Raised when a persisted state record does not match the schema contract."""


class State(str, Enum):
    NEW = "NEW"
    INTAKING = "INTAKING"
    AWAITING_SPEC_APPROVAL = "AWAITING_SPEC_APPROVAL"
    PLANNING = "PLANNING"
    AWAITING_PLAN_APPROVAL = "AWAITING_PLAN_APPROVAL"
    BASE_REVALIDATION = "BASE_REVALIDATION"
    MATERIALIZING_WORKTREE = "MATERIALIZING_WORKTREE"
    EXECUTING = "EXECUTING"
    HANDOFF = "HANDOFF"
    READY = "READY"
    BLOCKED = "BLOCKED"
    ABANDONED = "ABANDONED"


ACTIVE_STATES = frozenset(
    {
        State.NEW,
        State.INTAKING,
        State.AWAITING_SPEC_APPROVAL,
        State.PLANNING,
        State.AWAITING_PLAN_APPROVAL,
        State.BASE_REVALIDATION,
        State.MATERIALIZING_WORKTREE,
        State.EXECUTING,
        State.HANDOFF,
    }
)


# A blocked task may resume at its current checkpoint or at a narrowly scoped
# recovery checkpoint.  Keeping this table explicit prevents callers from
# smuggling a task into a later lifecycle stage via ``resume_from``.
SAFE_BLOCK_RESUME_STATES = {
    State.NEW: frozenset({State.NEW}),
    State.INTAKING: frozenset({State.INTAKING}),
    State.AWAITING_SPEC_APPROVAL: frozenset(
        {State.INTAKING, State.AWAITING_SPEC_APPROVAL}
    ),
    State.PLANNING: frozenset({State.PLANNING}),
    State.AWAITING_PLAN_APPROVAL: frozenset(
        {State.PLANNING, State.AWAITING_PLAN_APPROVAL}
    ),
    State.BASE_REVALIDATION: frozenset(
        {State.PLANNING, State.BASE_REVALIDATION}
    ),
    State.MATERIALIZING_WORKTREE: frozenset(
        {State.PLANNING, State.MATERIALIZING_WORKTREE}
    ),
    State.EXECUTING: frozenset({State.PLANNING, State.EXECUTING}),
    State.HANDOFF: frozenset({State.PLANNING, State.EXECUTING, State.HANDOFF}),
}


LEGAL_TRANSITIONS = {
    State.NEW: {State.INTAKING, State.BLOCKED, State.ABANDONED},
    State.INTAKING: {
        State.AWAITING_SPEC_APPROVAL,
        State.BLOCKED,
        State.ABANDONED,
    },
    State.AWAITING_SPEC_APPROVAL: {
        State.PLANNING,
        State.READY,
        State.BLOCKED,
        State.ABANDONED,
    },
    State.PLANNING: {
        State.AWAITING_PLAN_APPROVAL,
        State.BLOCKED,
        State.ABANDONED,
    },
    State.AWAITING_PLAN_APPROVAL: {
        State.BASE_REVALIDATION,
        State.READY,
        State.BLOCKED,
        State.ABANDONED,
    },
    State.BASE_REVALIDATION: {
        State.MATERIALIZING_WORKTREE,
        State.BLOCKED,
        State.ABANDONED,
    },
    State.MATERIALIZING_WORKTREE: {
        State.EXECUTING,
        State.BLOCKED,
        State.ABANDONED,
    },
    State.EXECUTING: {State.HANDOFF, State.BLOCKED, State.ABANDONED},
    State.HANDOFF: {State.READY, State.BLOCKED, State.ABANDONED},
    State.READY: set(),
    State.BLOCKED: {State.ABANDONED},
    State.ABANDONED: set(),
}


StateLike = Union[State, str]
Record = Dict[str, Any]


def validate_task_id(task_id: str) -> str:
    """Return *task_id* when it is a safe single path component."""

    if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
        raise InvalidTaskId(
            "태스크 ID는 1~64자여야 하며 영문자나 숫자로 시작하고 "
            "영문자, 숫자, '.', '_', '-'만 사용할 수 있습니다"
        )
    return task_id


def discover_git_common_dir(repo: Optional[Union[str, Path]] = None) -> Path:
    """Resolve the common Git directory shared by the repository's worktrees."""

    cwd = Path(repo or os.getcwd()).expanduser().resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(cwd),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise SCVStateError(
            "{}의 Git common directory를 확인할 수 없습니다: {}".format(
                cwd, detail.strip()
            )
        ) from exc

    raw = result.stdout.strip()
    if not raw:
        raise SCVStateError("git rev-parse가 빈 common directory를 반환했습니다")
    common_dir = Path(raw).expanduser()
    if not common_dir.is_absolute():
        common_dir = cwd / common_dir
    return common_dir.resolve()


def default_state_root(repo: Optional[Union[str, Path]] = None) -> Path:
    """Return ``<git-common-dir>/scv/tasks`` for *repo*."""

    return discover_git_common_dir(repo) / "scv" / "tasks"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _coerce_state(value: StateLike) -> State:
    if isinstance(value, State):
        return value
    try:
        return State(value)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(state.value for state in State)
        raise InvalidTransition("알 수 없는 상태 {!r}입니다. 가능한 상태: {}".format(value, choices)) from exc


def _validate_text(value: str, label: str, *, max_length: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} 값은 비어 있을 수 없습니다".format(label))
    if len(value) > max_length or any(ord(char) < 32 for char in value):
        raise ValueError("{} 값에 제어 문자가 있거나 길이 제한을 초과했습니다".format(label))
    return value


def _validate_sha(value: str) -> str:
    if not isinstance(value, str) or not COMMIT_PATTERN.fullmatch(value):
        raise ValueError("기준 SHA는 7~64자의 16진수 커밋 ID여야 합니다")
    return value.lower()


def _validate_json_value(value: Any) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("산출물 값은 올바른 JSON이어야 합니다: {}".format(exc)) from exc


class TaskStateStore:
    """Atomic JSON store for SCV task lifecycle records."""

    def __init__(
        self,
        repo: Optional[Union[str, Path]] = None,
        state_root: Optional[Union[str, Path]] = None,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        try:
            require_macos()
        except RuntimeRequirementError as exc:
            raise SCVStateError(str(exc)) from exc
        self.repo = Path(repo or os.getcwd()).expanduser().resolve()
        self.state_root = (
            Path(state_root).expanduser().resolve()
            if state_root is not None
            else default_state_root(self.repo)
        )
        self._clock = clock or _utc_now

    def task_dir(self, task_id: str) -> Path:
        """Return the validated task directory without creating it."""

        return self.state_root / validate_task_id(task_id)

    def state_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "state.json"

    def create(
        self,
        task_id: str,
        *,
        target: str,
        base_branch: str,
        base_sha: str,
        artifacts: Optional[Mapping[str, Any]] = None,
        initial_state: StateLike = State.NEW,
    ) -> Record:
        """Create a task without replacing existing state.

        ``initial_state`` is deliberately limited to ``NEW`` and ``INTAKING``.
        The latter lets the controller publish a started task in one durable
        write, so an interruption cannot strand it between create and start.
        """

        validate_task_id(task_id)
        if target not in TARGETS:
            raise ValueError("target은 analyze, plan, full 중 하나여야 합니다")
        created_state = _coerce_state(initial_state)
        if created_state not in {State.NEW, State.INTAKING}:
            raise InvalidTransition("태스크 최초 상태는 NEW 또는 INTAKING이어야 합니다")
        base_branch = _validate_text(base_branch, "기준 브랜치", max_length=255)
        base_sha = _validate_sha(base_sha)
        artifact_values: Dict[str, Any] = {}
        for name, value in (artifacts or {}).items():
            self._validate_artifact(name, value)
            artifact_values[name] = copy.deepcopy(value)

        now = self._now()
        record: Record = {
            "schema_version": SCHEMA_VERSION,
            "revision": 1,
            "task_id": task_id,
            "target": target,
            "state": created_state.value,
            "base": {"branch": base_branch, "sha": base_sha},
            "artifacts": artifact_values,
            "worktree": {"path": None, "branch": None},
            "resume": None,
            "timestamps": {
                "created_at": now,
                "updated_at": now,
                "state_entered_at": now,
                "state_entries": {created_state.value: now},
                "ready_at": None,
                "abandoned_at": None,
            },
            "history": [
                {
                    "at": now,
                    "event": "created",
                    "from": None,
                    "to": created_state.value,
                    "note": None,
                }
            ],
        }
        directory = self.task_dir(task_id)
        with self._task_lock(task_id):
            try:
                directory.mkdir(parents=True, exist_ok=False)
            except FileExistsError as exc:
                raise TaskExists("태스크 {!r}가 이미 존재합니다".format(task_id)) from exc

            try:
                self._atomic_write(self.state_path(task_id), record)
            except BaseException:
                # Creation owns the directory and can safely remove it when no
                # state was published.  Keep the persistent lock file: deleting a
                # lock inode could split future contenders across two locks.
                try:
                    directory.rmdir()
                except OSError:
                    pass
                raise
        return copy.deepcopy(record)

    def load(self, task_id: str) -> Record:
        """Load and validate the authoritative task record."""

        path = self.state_path(task_id)
        try:
            with path.open("r", encoding="utf-8") as handle:
                record = json.load(handle)
        except FileNotFoundError as exc:
            raise TaskNotFound("태스크 {!r}가 존재하지 않습니다".format(task_id)) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise CorruptState("{} 상태 파일을 읽을 수 없습니다: {}".format(path, exc)) from exc
        self._validate_record(record, expected_task_id=task_id)
        return copy.deepcopy(record)

    def transition(
        self, task_id: str, to_state: StateLike, *, note: Optional[str] = None
    ) -> Record:
        """Advance a task through a legal non-blocking lifecycle edge."""

        target_state = _coerce_state(to_state)
        if target_state is State.BLOCKED:
            raise InvalidTransition("재개 상태와 사유를 저장하려면 block()을 사용해야 합니다")

        def mutate(record: Record, now: str) -> None:
            self._apply_transition(record, target_state, now, note=note)

        return self._mutate(task_id, mutate)

    def record_artifact(
        self,
        task_id: str,
        name: str,
        value: Any,
        *,
        transition_to: Optional[StateLike] = None,
        note: Optional[str] = None,
    ) -> Record:
        """Persist an artifact and optional state transition in one atomic revision."""

        self._validate_artifact(name, value)
        target_state = _coerce_state(transition_to) if transition_to is not None else None
        if target_state is State.BLOCKED:
            raise InvalidTransition("BLOCKED 상태로 전환하려면 block()을 사용해야 합니다")

        def mutate(record: Record, now: str) -> None:
            record["artifacts"][name] = copy.deepcopy(value)
            record["history"].append(
                {
                    "at": now,
                    "event": "artifact_recorded",
                    "name": name,
                    "note": note,
                }
            )
            if target_state is not None:
                self._apply_transition(record, target_state, now, note=note)

        return self._mutate(task_id, mutate)

    def set_artifact(self, task_id: str, name: str, value: Any) -> Record:
        return self.record_artifact(task_id, name, value)

    def set_worktree(self, task_id: str, *, path: Union[str, Path], branch: str) -> Record:
        """Record the materialized worktree identity without creating it."""

        path_text = _validate_text(str(path), "워크트리 경로", max_length=4096)
        branch = _validate_text(branch, "워크트리 브랜치", max_length=255)
        resolved_path = str(Path(path_text).expanduser().resolve())

        def mutate(record: Record, now: str) -> None:
            if State(record["state"]) is State.ABANDONED:
                raise InvalidTransition("포기한 태스크의 워크트리는 갱신할 수 없습니다")
            record["worktree"] = {"path": resolved_path, "branch": branch}
            record["history"].append(
                {
                    "at": now,
                    "event": "worktree_recorded",
                    "path": resolved_path,
                    "branch": branch,
                }
            )

        return self._mutate(task_id, mutate)

    def update_base(self, task_id: str, *, branch: str, sha: str) -> Record:
        """Replace the captured base after an explicit revalidation decision."""

        branch = _validate_text(branch, "기준 브랜치", max_length=255)
        sha = _validate_sha(sha)

        def mutate(record: Record, now: str) -> None:
            if State(record["state"]) in {State.READY, State.ABANDONED}:
                raise InvalidTransition("종료된 태스크의 기준 리비전은 갱신할 수 없습니다")
            previous = copy.deepcopy(record["base"])
            record["base"] = {"branch": branch, "sha": sha}
            record["history"].append(
                {
                    "at": now,
                    "event": "base_updated",
                    "previous": previous,
                    "base": copy.deepcopy(record["base"]),
                }
            )

        return self._mutate(task_id, mutate)

    def invalidate_base(
        self,
        task_id: str,
        *,
        branch: str,
        sha: str,
        reason: str = "승인된 기준 리비전이 변경되어 계획을 다시 승인해야 합니다",
    ) -> Record:
        """Atomically invalidate an approved plan after base revision drift.

        The base identity, ``base_change`` evidence, plan approval, and blocked
        recovery checkpoint are committed as one revision under one task lock.
        """

        branch = _validate_text(branch, "기준 브랜치", max_length=255)
        sha = _validate_sha(sha)
        reason = _validate_text(reason, "차단 사유")

        def mutate(record: Record, now: str) -> None:
            current = State(record["state"])
            if current not in {
                State.BASE_REVALIDATION,
                State.MATERIALIZING_WORKTREE,
            }:
                raise InvalidTransition(
                    "기준 리비전 무효화는 BASE_REVALIDATION 또는 "
                    "MATERIALIZING_WORKTREE 상태에서만 가능합니다"
                )

            previous = copy.deepcopy(record["base"])
            if previous["branch"] == branch and previous["sha"] == sha:
                raise InvalidTransition("변경되지 않은 기준 리비전은 무효화할 수 없습니다")

            record["base"] = {"branch": branch, "sha": sha}
            record["artifacts"]["base_change"] = {
                "previous_sha": previous["sha"],
                "current_sha": sha,
                "detected_at": now,
            }
            approval = record["artifacts"].get("plan_approval")
            if not isinstance(approval, MutableMapping):
                approval = {}
            else:
                approval = copy.deepcopy(approval)
            approval["approved"] = False
            approval["invalidated_at"] = now
            record["artifacts"]["plan_approval"] = approval
            record["history"].append(
                {
                    "at": now,
                    "event": "base_invalidated",
                    "previous": previous,
                    "base": copy.deepcopy(record["base"]),
                }
            )
            record["resume"] = {
                "resume_from": State.PLANNING.value,
                "blocked_from": current.value,
                "reason": reason,
                "blocked_at": now,
                "resumed_at": None,
            }
            self._apply_transition(
                record,
                State.BLOCKED,
                now,
                note=reason,
                allow_block=True,
            )

        return self._mutate(task_id, mutate)

    def block(
        self,
        task_id: str,
        *,
        reason: str,
        resume_from: Optional[StateLike] = None,
    ) -> Record:
        """Enter ``BLOCKED`` and save the state used by :meth:`resume`."""

        reason = _validate_text(reason, "차단 사유")
        requested_resume = _coerce_state(resume_from) if resume_from is not None else None
        if requested_resume is not None and requested_resume not in ACTIVE_STATES:
            raise InvalidTransition("resume_from에는 진행 가능한 워크플로 상태가 필요합니다")

        def mutate(record: Record, now: str) -> None:
            current = State(record["state"])
            if current not in ACTIVE_STATES:
                raise InvalidTransition("{} 상태의 태스크는 차단할 수 없습니다".format(current.value))
            return_state = requested_resume or current
            allowed = SAFE_BLOCK_RESUME_STATES[current]
            if return_state not in allowed:
                choices = ", ".join(sorted(state.value for state in allowed))
                raise InvalidTransition(
                    "{} 상태에서 재개할 수 있는 상태는 {}입니다".format(
                        current.value, choices
                    )
                )
            record["resume"] = {
                "resume_from": return_state.value,
                "blocked_from": current.value,
                "reason": reason,
                "blocked_at": now,
                "resumed_at": None,
            }
            self._apply_transition(
                record,
                State.BLOCKED,
                now,
                note=reason,
                allow_block=True,
            )

        return self._mutate(task_id, mutate)

    def resume(self, task_id: str, *, note: Optional[str] = None) -> Record:
        """Continue a blocked task or promote a completed partial target.

        ``READY/analyze`` promotes to ``plan`` at ``PLANNING``;
        ``READY/plan`` promotes to ``full`` at ``BASE_REVALIDATION``; and a
        completed ``full`` task is returned unchanged.
        """

        current_record = self.load(task_id)
        current = State(current_record["state"])
        if current is State.READY and current_record["target"] == "full":
            return current_record

        def mutate(record: Record, now: str) -> None:
            state = State(record["state"])
            if state is State.BLOCKED:
                resume_info = record.get("resume")
                if not isinstance(resume_info, MutableMapping):
                    raise CorruptState("BLOCKED 태스크에 재개 정보가 없습니다")
                return_state = _coerce_state(resume_info.get("resume_from"))
                if return_state not in ACTIVE_STATES:
                    raise CorruptState("BLOCKED 태스크의 재개 상태가 올바르지 않습니다")
                self._apply_transition(
                    record,
                    return_state,
                    now,
                    note=note,
                    allow_resume=True,
                )
                resume_info["resumed_at"] = now
                return

            if state is State.READY and record["target"] == "analyze":
                previous_target = record["target"]
                record["target"] = "plan"
                self._apply_transition(
                    record,
                    State.PLANNING,
                    now,
                    note=note or "analyze 태스크를 plan 목표로 승격했습니다",
                    allow_promotion=True,
                )
                record["history"].append(
                    {
                        "at": now,
                        "event": "target_promoted",
                        "from": previous_target,
                        "to": "plan",
                    }
                )
                return

            if state is State.READY and record["target"] == "plan":
                previous_target = record["target"]
                record["target"] = "full"
                self._apply_transition(
                    record,
                    State.BASE_REVALIDATION,
                    now,
                    note=note or "plan 태스크를 full 목표로 승격했습니다",
                    allow_promotion=True,
                )
                record["history"].append(
                    {
                        "at": now,
                        "event": "target_promoted",
                        "from": previous_target,
                        "to": "full",
                    }
                )
                return

            raise InvalidTransition("{} 상태의 태스크는 재개할 수 없습니다".format(state.value))

        return self._mutate(task_id, mutate)

    def abandon(self, task_id: str, *, reason: Optional[str] = None) -> Record:
        """Mark a task abandoned without deleting its evidence or worktree."""

        if reason is not None:
            reason = _validate_text(reason, "포기 사유")

        def mutate(record: Record, now: str) -> None:
            current = State(record["state"])
            if current is State.ABANDONED:
                raise InvalidTransition("태스크가 이미 ABANDONED 상태입니다")
            self._apply_transition(
                record,
                State.ABANDONED,
                now,
                note=reason,
                allow_abandon=True,
            )

        return self._mutate(task_id, mutate)

    def list_tasks(self) -> List[Record]:
        """Return all task records sorted by task ID."""

        if not self.state_root.exists():
            return []
        records = []
        for state_path in sorted(self.state_root.glob("*/state.json")):
            records.append(self.load(state_path.parent.name))
        return records

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, str) or not value:
            raise SCVStateError("clock은 비어 있지 않은 타임스탬프 문자열을 반환해야 합니다")
        return value

    @staticmethod
    def _validate_artifact(name: str, value: Any) -> None:
        if not isinstance(name, str) or not ARTIFACT_NAME_PATTERN.fullmatch(name):
            raise ValueError(
                "산출물 이름은 영문자나 숫자로 시작하는 1~64자의 안전한 문자열이어야 합니다"
            )
        _validate_json_value(value)

    def _mutate(
        self,
        task_id: str,
        mutator: Callable[[Record, str], None],
    ) -> Record:
        with self._task_lock(task_id):
            record = self.load(task_id)
            now = self._now()
            mutator(record, now)
            record["revision"] += 1
            record["timestamps"]["updated_at"] = now
            self._validate_record(record, expected_task_id=task_id)
            self._atomic_write(self.state_path(task_id), record)
            return copy.deepcopy(record)

    @contextmanager
    def _task_lock(self, task_id: str) -> Iterator[None]:
        """Hold a process-wide exclusive lock for one task record.

        Lock files live outside task directories so concurrent creation can be
        serialized before either contender publishes the directory.  They are
        intentionally retained: unlinking an active lock file can let another
        process lock a different inode for the same task.
        """

        if fcntl is None:  # pragma: no cover - 생성자에서 macOS를 먼저 검사합니다.
            raise SCVStateError("SCV 상태 잠금은 macOS에서만 사용할 수 있습니다")
        validate_task_id(task_id)
        lock_directory = self.state_root / ".locks"
        lock_path = lock_directory / "{}.lock".format(task_id)
        try:
            lock_directory.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise SCVStateError(
                "태스크 {!r}의 상태 잠금 파일을 준비할 수 없습니다: {}".format(
                    task_id, exc
                )
            ) from exc

        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except OSError as exc:
                raise SCVStateError(
                    "태스크 {!r}의 상태 잠금을 획득할 수 없습니다: {}".format(
                        task_id, exc
                    )
                ) from exc
            try:
                yield
            finally:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    # Closing the descriptor also releases flock.  Do not mask a
                    # more useful mutation error with a best-effort unlock error.
                    pass
        finally:
            os.close(descriptor)

    def _apply_transition(
        self,
        record: Record,
        target: State,
        now: str,
        *,
        note: Optional[str],
        allow_block: bool = False,
        allow_resume: bool = False,
        allow_promotion: bool = False,
        allow_abandon: bool = False,
    ) -> None:
        current = State(record["state"])
        if target is current:
            raise InvalidTransition("태스크가 이미 {} 상태입니다".format(target.value))

        permitted = target in LEGAL_TRANSITIONS[current]
        if current is State.BLOCKED and allow_resume:
            permitted = target in ACTIVE_STATES
        if current is State.READY and allow_promotion:
            permitted = target in {State.PLANNING, State.BASE_REVALIDATION}
        if allow_abandon and target is State.ABANDONED:
            permitted = current not in {State.READY, State.ABANDONED}
        if target is State.BLOCKED and not allow_block:
            permitted = False
        if not permitted:
            raise InvalidTransition(
                "허용되지 않는 상태 전이입니다: {} -> {}".format(current.value, target.value)
            )

        self._validate_target_edge(record["target"], current, target)
        record["state"] = target.value
        record["timestamps"]["state_entered_at"] = now
        record["timestamps"]["state_entries"][target.value] = now
        if target is State.READY:
            record["timestamps"]["ready_at"] = now
        if target is State.ABANDONED:
            record["timestamps"]["abandoned_at"] = now
        record["history"].append(
            {
                "at": now,
                "event": "transition",
                "from": current.value,
                "to": target.value,
                "note": note,
            }
        )

    @staticmethod
    def _validate_target_edge(target: str, current: State, destination: State) -> None:
        if current is State.AWAITING_SPEC_APPROVAL:
            if destination is State.READY and target != "analyze":
                raise InvalidTransition("스펙 승인 후 바로 완료할 수 있는 목표는 analyze뿐입니다")
            if destination is State.PLANNING and target not in {"plan", "full"}:
                raise InvalidTransition("analyze 목표는 계획 단계로 진입할 수 없습니다")
        if current is State.AWAITING_PLAN_APPROVAL:
            if destination is State.READY and target != "plan":
                raise InvalidTransition("계획 승인 후 바로 완료할 수 있는 목표는 plan뿐입니다")
            if destination is State.BASE_REVALIDATION and target != "full":
                raise InvalidTransition("기준 리비전 재검증 단계로 진입할 수 있는 목표는 full뿐입니다")
        if current is State.HANDOFF and destination is State.READY and target != "full":
            raise InvalidTransition("인계 단계를 완료할 수 있는 목표는 full뿐입니다")

    @staticmethod
    def _validate_record(record: Any, *, expected_task_id: str) -> None:
        if not isinstance(record, dict):
            raise CorruptState("상태 레코드는 JSON 객체여야 합니다")
        if record.get("schema_version") != SCHEMA_VERSION:
            raise CorruptState("schema_version이 없거나 지원되지 않습니다")
        if record.get("task_id") != expected_task_id:
            raise CorruptState("상태의 task_id가 디렉터리 이름과 일치하지 않습니다")
        validate_task_id(expected_task_id)
        if record.get("target") not in TARGETS:
            raise CorruptState("상태 레코드의 target이 올바르지 않습니다")
        try:
            State(record.get("state"))
        except (TypeError, ValueError) as exc:
            raise CorruptState("상태 레코드의 생명주기 상태가 올바르지 않습니다") from exc
        if not isinstance(record.get("revision"), int) or record["revision"] < 1:
            raise CorruptState("상태 revision은 양의 정수여야 합니다")
        base = record.get("base")
        if not isinstance(base, dict) or set(base) != {"branch", "sha"}:
            raise CorruptState("상태 레코드의 기준 리비전 정보가 올바르지 않습니다")
        try:
            _validate_text(base["branch"], "기준 브랜치", max_length=255)
            _validate_sha(base["sha"])
        except ValueError as exc:
            raise CorruptState(str(exc)) from exc
        if not isinstance(record.get("artifacts"), dict):
            raise CorruptState("artifacts는 JSON 객체여야 합니다")
        if not isinstance(record.get("worktree"), dict):
            raise CorruptState("worktree는 JSON 객체여야 합니다")
        if not isinstance(record.get("timestamps"), dict):
            raise CorruptState("timestamps는 JSON 객체여야 합니다")
        if not isinstance(record.get("history"), list):
            raise CorruptState("history는 JSON 배열이어야 합니다")
        resume = record.get("resume")
        if resume is not None:
            if not isinstance(resume, dict):
                raise CorruptState("resume은 JSON 객체 또는 null이어야 합니다")
            try:
                blocked_from = State(resume.get("blocked_from"))
                resume_from = State(resume.get("resume_from"))
            except (TypeError, ValueError) as exc:
                raise CorruptState("resume의 상태 정보가 올바르지 않습니다") from exc
            if (
                blocked_from not in SAFE_BLOCK_RESUME_STATES
                or resume_from not in SAFE_BLOCK_RESUME_STATES[blocked_from]
            ):
                raise CorruptState("resume에 안전하지 않은 복구 상태가 기록되어 있습니다")
        if State(record["state"]) is State.BLOCKED and resume is None:
            raise CorruptState("BLOCKED 태스크에 재개 정보가 없습니다")

    @staticmethod
    def _atomic_write(path: Path, record: Record) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".state.", suffix=".tmp", dir=str(path.parent)
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2, sort_keys=True, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            try:
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _json_print(value: Any) -> None:
    print(
        json.dumps(
            decorate_scv_output(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    )


def _build_parser() -> argparse.ArgumentParser:
    localize_argparse()
    parser = argparse.ArgumentParser(description="Codex SCV 태스크 상태를 관리합니다")
    parser.add_argument(
        "--repo", default=os.getcwd(), metavar="저장소", help="저장소 또는 워크트리 경로"
    )
    parser.add_argument(
        "--state-root", metavar="상태-루트", help="태스크 상태 루트 재정의(주로 테스트용)"
    )
    subparsers = parser.add_subparsers(dest="command")

    create = subparsers.add_parser("create", help="NEW 태스크를 생성합니다")
    create.add_argument("task_id", metavar="태스크-ID")
    create.add_argument("--target", metavar="목표", choices=sorted(TARGETS), required=True)
    create.add_argument("--base-branch", metavar="기준-브랜치", required=True)
    create.add_argument("--base-sha", metavar="기준-SHA", required=True)

    show = subparsers.add_parser("show", help="태스크 하나를 표시합니다")
    show.add_argument("task_id", metavar="태스크-ID")

    transition = subparsers.add_parser("transition", help="허용된 상태 전이를 수행합니다")
    transition.add_argument("task_id", metavar="태스크-ID")
    transition.add_argument("state", metavar="상태", choices=[state.value for state in State])
    transition.add_argument("--note", metavar="메모")

    artifact = subparsers.add_parser("artifact", help="산출물 값을 기록합니다")
    artifact.add_argument("task_id", metavar="태스크-ID")
    artifact.add_argument("name", metavar="이름")
    artifact.add_argument("value", metavar="값")
    artifact.add_argument("--json-value", action="store_true")
    artifact.add_argument("--transition-to", metavar="전이-상태", choices=[state.value for state in State])
    artifact.add_argument("--note", metavar="메모")

    worktree = subparsers.add_parser("worktree", help="워크트리를 기록합니다")
    worktree.add_argument("task_id", metavar="태스크-ID")
    worktree.add_argument("--path", metavar="경로", required=True)
    worktree.add_argument("--branch", metavar="브랜치", required=True)

    base = subparsers.add_parser("base", help="기록된 기준 리비전을 갱신합니다")
    base.add_argument("task_id", metavar="태스크-ID")
    base.add_argument("--branch", metavar="브랜치", required=True)
    base.add_argument("--sha", metavar="SHA", required=True)

    block = subparsers.add_parser("block", help="복구 가능한 차단 사유를 기록합니다")
    block.add_argument("task_id", metavar="태스크-ID")
    block.add_argument("--reason", metavar="사유", required=True)
    block.add_argument(
        "--resume-from", metavar="재개-상태", choices=[state.value for state in ACTIVE_STATES]
    )

    resume = subparsers.add_parser("resume", help="태스크를 재개하거나 목표를 승격합니다")
    resume.add_argument("task_id", metavar="태스크-ID")
    resume.add_argument("--note", metavar="메모")

    abandon = subparsers.add_parser("abandon", help="태스크를 포기 상태로 기록합니다")
    abandon.add_argument("task_id", metavar="태스크-ID")
    abandon.add_argument("--reason", metavar="사유")

    subparsers.add_parser("list", help="태스크 레코드 목록을 표시합니다")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    if arguments.command is None:
        parser.error("명령을 지정해야 합니다")
    try:
        require_macos()
        store = TaskStateStore(repo=arguments.repo, state_root=arguments.state_root)
        if arguments.command == "create":
            result = store.create(
                arguments.task_id,
                target=arguments.target,
                base_branch=arguments.base_branch,
                base_sha=arguments.base_sha,
            )
        elif arguments.command == "show":
            result = store.load(arguments.task_id)
        elif arguments.command == "transition":
            result = store.transition(arguments.task_id, arguments.state, note=arguments.note)
        elif arguments.command == "artifact":
            value = json.loads(arguments.value) if arguments.json_value else arguments.value
            result = store.record_artifact(
                arguments.task_id,
                arguments.name,
                value,
                transition_to=arguments.transition_to,
                note=arguments.note,
            )
        elif arguments.command == "worktree":
            result = store.set_worktree(
                arguments.task_id, path=arguments.path, branch=arguments.branch
            )
        elif arguments.command == "base":
            result = store.update_base(
                arguments.task_id, branch=arguments.branch, sha=arguments.sha
            )
        elif arguments.command == "block":
            result = store.block(
                arguments.task_id,
                reason=arguments.reason,
                resume_from=arguments.resume_from,
            )
        elif arguments.command == "resume":
            result = store.resume(arguments.task_id, note=arguments.note)
        elif arguments.command == "abandon":
            result = store.abandon(arguments.task_id, reason=arguments.reason)
        elif arguments.command == "list":
            result = store.list_tasks()
        else:  # pragma: no cover - argparse enforces a known subcommand.
            parser.error("알 수 없는 명령입니다")
        _json_print(result)
        return 0
    except (
        SCVStateError,
        RuntimeRequirementError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print("scv 상태 오류: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
