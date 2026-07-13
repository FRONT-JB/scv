#!/usr/bin/env python3
"""SCV's provider-neutral control plane with a Codex execution backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - macOS 전용 오류를 먼저 표시합니다.
    fcntl = None  # type: ignore[assignment]

try:  # Support both direct CLI execution and module-based tests.
    from .scv_state import SCVStateError, State, TaskStateStore
    from .cli_ko import localize_argparse
    from .execute import (
        EXECUTION_BUSY_EXIT_CODE,
        ExecutionBusy,
        ExecutorError,
        PlanError,
        load_plan,
        locked_status,
        preflight_start_runtime,
    )
    from .runtime import RuntimeRequirementError, require_macos
    from .learning import LearningError, LearningStore
    from .scv_dialogue import decorate_scv_output
    from .workspace import workspace_fingerprint
except ImportError:  # pragma: no cover - exercised by direct script invocation.
    from scv_state import SCVStateError, State, TaskStateStore
    from cli_ko import localize_argparse
    from execute import (
        EXECUTION_BUSY_EXIT_CODE,
        ExecutionBusy,
        ExecutorError,
        PlanError,
        load_plan,
        locked_status,
        preflight_start_runtime,
    )
    from runtime import RuntimeRequirementError, require_macos
    from learning import LearningError, LearningStore
    from scv_dialogue import decorate_scv_output
    from workspace import workspace_fingerprint


SCRIPT_DIR = Path(__file__).resolve().parent


class SCVError(RuntimeError):
    """A user-actionable control-plane failure."""


@contextmanager
def controller_execution_lease(task_directory: Path) -> Iterator[None]:
    """Keep one control-plane execute call active for a task at a time."""

    try:
        require_macos()
    except RuntimeRequirementError as exc:
        raise SCVError(str(exc)) from exc
    if fcntl is None:  # pragma: no cover - macOS에는 항상 존재합니다.
        raise SCVError("SCV 실행 잠금은 macOS에서만 사용할 수 있습니다")
    lock_path = task_directory / ".controller-execute.lock"
    descriptor: int | None = None
    try:
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(str(lock_path), flags, 0o600)
        os.fchmod(descriptor, 0o600)
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise SCVError(f"실행 잠금 파일을 준비할 수 없습니다: {exc}") from exc
    assert descriptor is not None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SCVError(
                "이 태스크는 다른 SCV 실행기가 처리 중입니다. 현재 상태는 변경하지 않았습니다"
            ) from exc
        except OSError as exc:
            raise SCVError(f"실행 잠금을 획득할 수 없습니다: {exc}") from exc
        try:
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_reason(value: object, fallback: str = "알 수 없는 차단 사유") -> str:
    """Normalize external error text for the state store's single-line contract."""

    printable = "".join(
        " " if ord(character) < 32 or ord(character) == 127 else character
        for character in str(value)
    )
    normalized = " ".join(printable.split())
    return (normalized or fallback)[:4000]


def state_name(task: dict[str, Any]) -> str:
    value = task.get("state")
    return value.value if isinstance(value, State) else str(value)


def emit(value: Any) -> None:
    print(
        json.dumps(
            decorate_scv_output(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SCVError(f"git {' '.join(args)} 실행 실패: {detail}")
    return result.stdout.strip()


def resolve_repo(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    root = git(candidate, "rev-parse", "--show-toplevel")
    return Path(root).resolve()


def resolve_base(repo: Path, requested: str | None) -> tuple[str, str]:
    branch = requested or git(repo, "branch", "--show-current")
    if not branch:
        raise SCVError("분리된 HEAD에서는 --base 브랜치를 명시해야 합니다")
    git(repo, "check-ref-format", "--branch", branch)
    sha = git(repo, "rev-parse", "--verify", f"{branch}^{{commit}}")
    return branch, sha


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def fingerprint(path: Path, task_dir: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(task_dir)),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "recorded_at": utc_now(),
    }


def approved_artifact(
    store: TaskStateStore,
    task: dict[str, Any],
    name: str,
    filename: str,
) -> dict[str, Any]:
    metadata = task.get("artifacts", {}).get(name)
    approval = task.get("artifacts", {}).get(f"{name}_approval")
    path = store.task_dir(task["task_id"]) / filename
    if not isinstance(metadata, dict) or not metadata.get("sha256"):
        raise SCVError(f"승인된 {name} 산출물 메타데이터가 없습니다")
    if (
        not isinstance(approval, dict)
        or approval.get("approved") is not True
        or approval.get("sha256") != metadata["sha256"]
    ):
        raise SCVError(f"현재 {name} 산출물이 명시적으로 승인되지 않았습니다")
    if not path.is_file():
        raise SCVError(f"승인된 {name} 파일이 없습니다: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != metadata["sha256"]:
        raise SCVError(f"승인 후 {name} 파일 내용이 변경되었습니다")
    return metadata


def require_state(task: dict[str, Any], allowed: Iterable[State]) -> State:
    current = State(state_name(task))
    accepted = set(allowed)
    if current not in accepted:
        names = ", ".join(sorted(item.value for item in accepted))
        raise SCVError(f"현재 태스크 상태는 {current.value}입니다. 가능한 상태: {names}")
    return current


def validate_plan(
    document: Any,
    task_id: str,
    expected_base_sha: str | None = None,
) -> dict[str, Any]:
    """Use the executor's exact schema before a plan can be submitted."""

    if not isinstance(document, dict):
        raise SCVError("계획은 JSON 객체여야 합니다")
    candidate = dict(document)
    if expected_base_sha is not None:
        candidate["expected_base_sha"] = expected_base_sha
    payload = (json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".scv-plan-", suffix=".json")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            parsed = load_plan(temporary, expected_base_sha)
        except PlanError as exc:
            raise SCVError(f"계획 검증 실패: {exc}") from exc
        if parsed.task_id != task_id:
            raise SCVError(f"plan.task_id는 {task_id!r}와 같아야 합니다")
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return candidate


def artifact_path(store: TaskStateStore, task_id: str, name: str) -> Path:
    return store.task_dir(task_id) / name


def execution_evidence_context(
    store: TaskStateStore,
    task_id: str,
    task: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    """Resolve execution evidence only when it is bound to the approved plan."""

    plan_metadata = approved_artifact(store, task, "plan", "plan.json")
    root = Path(task.get("worktree", {}).get("path", ""))
    if not root.is_dir():
        raise SCVError(f"기록된 워크트리를 사용할 수 없습니다: {root}")
    execution = task.get("artifacts", {}).get("execution")
    if not isinstance(execution, dict) or not execution.get("path"):
        raise SCVError("실행 증거 경로가 없습니다")
    expected_index = Path("runs") / plan_metadata["sha256"] / "index.json"
    if (
        execution.get("path") != str(expected_index)
        or execution.get("plan_sha256") != plan_metadata["sha256"]
    ):
        raise SCVError("실행 증거가 승인된 계획과 연결되지 않습니다")
    index_sha256 = execution.get("index_sha256")
    if (
        not isinstance(index_sha256, str)
        or len(index_sha256) != 64
        or any(character not in "0123456789abcdef" for character in index_sha256)
    ):
        raise SCVError("실행 인덱스 SHA-256이 없습니다")
    workspace_sha256 = execution.get("workspace_sha256")
    if (
        not isinstance(workspace_sha256, str)
        or len(workspace_sha256) != 64
        or any(character not in "0123456789abcdef" for character in workspace_sha256)
    ):
        raise SCVError("검증된 워크트리 SHA-256이 없습니다")
    run_dir = store.task_dir(task_id) / expected_index.parent
    return plan_metadata, execution, root, run_dir


def require_ready_execution_bindings(
    evidence: dict[str, Any],
    *,
    task_id: str,
    task: dict[str, Any],
    plan_metadata: dict[str, Any],
    root: Path,
    execution: dict[str, Any],
) -> None:
    bindings = {
        "task_id": task_id,
        "plan_sha256": plan_metadata["sha256"],
        "expected_base_sha": task["base"]["sha"],
        "workspace": str(root.resolve()),
        "workspace_sha256": execution["workspace_sha256"],
    }
    if any(evidence.get(name) != value for name, value in bindings.items()):
        raise SCVError("실행 증거의 태스크·계획·기준·워크트리 연결이 일치하지 않습니다")
    if evidence.get("status") != "ready":
        raise SCVError("실행 증거 상태가 ready가 아닙니다")
    if workspace_fingerprint(root) != execution["workspace_sha256"]:
        raise SCVError("실행 검증 이후 워크트리 내용이 변경되었습니다")


def require_execution_index_fingerprint(
    execution: dict[str, Any], run_dir: Path
) -> None:
    index_path = run_dir / "index.json"
    try:
        actual = hashlib.sha256(index_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise SCVError(f"실행 인덱스를 읽을 수 없습니다: {exc}") from exc
    if execution.get("index_sha256") != actual:
        raise SCVError("실행 이후 인덱스 내용이 변경되었습니다")


def command_start(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    branch, sha = resolve_base(repo, args.base)
    if args.request_file:
        request = Path(args.request_file).expanduser().read_text(encoding="utf-8")
    else:
        request = args.request
    if not request or not request.strip():
        raise SCVError("비어 있지 않은 --request 또는 --request-file이 필요합니다")
    return store.create(
        args.task_id,
        target=args.target,
        base_branch=branch,
        base_sha=sha,
        artifacts={"request": {"text": request, "recorded_at": utc_now()}},
        initial_state=State.INTAKING,
    )


def command_status(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    task = store.load(args.task_id)
    if task.get("target") == "full" and state_name(task) in {
        State.HANDOFF.value,
        State.READY.value,
    }:
        try:
            plan_metadata, execution, root, run_dir = execution_evidence_context(
                store, args.task_id, task
            )
            with locked_status(run_dir) as evidence:
                require_ready_execution_bindings(
                    evidence,
                    task_id=args.task_id,
                    task=task,
                    plan_metadata=plan_metadata,
                    root=root,
                    execution=execution,
                )
                require_execution_index_fingerprint(execution, run_dir)
        except ExecutionBusy as exc:
            raise SCVError(
                "다른 SCV 실행기가 실행 증거를 갱신 중이어서 상태를 확정할 수 없습니다"
            ) from exc
        except (ExecutorError, OSError, ValueError) as exc:
            raise SCVError(f"실행 증거 무결성 검증에 실패했습니다: {exc}") from exc
        task["execution_integrity"] = {
            "status": "verified",
            "index": str(run_dir / "index.json"),
        }
    task["task_dir"] = str(store.task_dir(args.task_id))
    return task


def command_submit_spec(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    task = store.load(args.task_id)
    current = require_state(task, (State.INTAKING, State.AWAITING_SPEC_APPROVAL))
    source = Path(args.spec).expanduser().resolve()
    if not source.is_file():
        raise SCVError(f"스펙 파일이 없습니다: {source}")
    try:
        source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SCVError(f"스펙은 올바른 UTF-8 텍스트여야 합니다: {exc}") from exc
    destination = artifact_path(store, args.task_id, "spec.md")
    atomic_write(destination, source.read_bytes())
    value = fingerprint(destination, store.task_dir(args.task_id))
    store.set_artifact(args.task_id, "spec_approval", {"approved": False})
    if current is State.INTAKING:
        return store.record_artifact(
            args.task_id,
            "spec",
            value,
            transition_to=State.AWAITING_SPEC_APPROVAL,
            note="스펙을 승인 대기 상태로 제출했습니다",
        )
    return store.set_artifact(args.task_id, "spec", value)


def command_approve_spec(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    task = store.load(args.task_id)
    require_state(task, (State.AWAITING_SPEC_APPROVAL,))
    spec = task.get("artifacts", {}).get("spec")
    if not isinstance(spec, dict) or not spec.get("sha256"):
        raise SCVError("현재 스펙 산출물을 찾을 수 없습니다")
    spec_path = artifact_path(store, args.task_id, "spec.md")
    if not spec_path.is_file() or hashlib.sha256(spec_path.read_bytes()).hexdigest() != spec["sha256"]:
        raise SCVError("제출 후 스펙 파일 내용이 변경되었습니다. 다시 제출해 주세요")
    destination = State.READY if task["target"] == "analyze" else State.PLANNING
    approval = {"approved": True, "sha256": spec["sha256"], "approved_at": utc_now()}
    return store.record_artifact(
        args.task_id,
        "spec_approval",
        approval,
        transition_to=destination,
        note="현재 스펙이 명시적으로 승인되었습니다",
    )


def command_submit_plan(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    task = store.load(args.task_id)
    current = require_state(task, (State.PLANNING, State.AWAITING_PLAN_APPROVAL))
    approved_artifact(store, task, "spec", "spec.md")
    source = Path(args.plan).expanduser().resolve()
    if not source.is_file():
        raise SCVError(f"계획 파일이 없습니다: {source}")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SCVError(f"계획이 올바른 UTF-8 JSON이 아닙니다: {exc}") from exc
    document = validate_plan(document, args.task_id, task["base"]["sha"])
    destination = artifact_path(store, args.task_id, "plan.json")
    atomic_write(
        destination,
        (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )
    value = fingerprint(destination, store.task_dir(args.task_id))
    previous_failure = task.get("artifacts", {}).get("execution_failure")
    if (
        isinstance(previous_failure, dict)
        and previous_failure.get("attempts_exhausted") is True
        and previous_failure.get("plan_sha256") == value["sha256"]
    ):
        raise SCVError("실행 시도 횟수를 모두 사용한 계획과 내용이 같습니다. 계획을 수정해 주세요")
    store.set_artifact(args.task_id, "plan_approval", {"approved": False})
    if current is State.PLANNING:
        return store.record_artifact(
            args.task_id,
            "plan",
            value,
            transition_to=State.AWAITING_PLAN_APPROVAL,
            note="구현 계획을 승인 대기 상태로 제출했습니다",
        )
    return store.set_artifact(args.task_id, "plan", value)


def command_approve_plan(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    task = store.load(args.task_id)
    require_state(task, (State.AWAITING_PLAN_APPROVAL,))
    plan = task.get("artifacts", {}).get("plan")
    if not isinstance(plan, dict) or not plan.get("sha256"):
        raise SCVError("현재 계획 산출물을 찾을 수 없습니다")
    plan_path = artifact_path(store, args.task_id, "plan.json")
    if not plan_path.is_file() or hashlib.sha256(plan_path.read_bytes()).hexdigest() != plan["sha256"]:
        raise SCVError("제출 후 계획 파일 내용이 변경되었습니다. 다시 제출해 주세요")
    destination = State.READY if task["target"] == "plan" else State.BASE_REVALIDATION
    approval = {"approved": True, "sha256": plan["sha256"], "approved_at": utc_now()}
    return store.record_artifact(
        args.task_id,
        "plan_approval",
        approval,
        transition_to=destination,
        note="현재 구현 계획이 명시적으로 승인되었습니다",
    )


def parse_worktrees(repo: Path) -> list[dict[str, str]]:
    output = git(repo, "worktree", "list", "--porcelain")
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines() + [""]:
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    return records


def default_worktree(repo: Path, task_id: str) -> Path:
    records = parse_worktrees(repo)
    if not records:
        raise SCVError("git에서 기본 워크트리를 확인할 수 없습니다")
    primary = Path(records[0]["worktree"]).resolve()
    return primary.parent / ".scv-worktrees" / primary.name / task_id


def validate_branch(repo: Path, branch: str) -> None:
    git(repo, "check-ref-format", "--branch", branch)


def command_materialize(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    task = store.load(args.task_id)
    current = require_state(task, (State.BASE_REVALIDATION, State.MATERIALIZING_WORKTREE, State.EXECUTING))
    if current is State.EXECUTING:
        return task

    try:
        approved_artifact(store, task, "plan", "plan.json")
    except SCVError as exc:
        store.block(args.task_id, reason=safe_reason(exc), resume_from=State.PLANNING)
        raise SCVError(f"{exc}. 태스크를 BLOCKED로 전환했습니다") from exc

    branch_now = task["base"]["branch"]
    sha_now = git(repo, "rev-parse", "--verify", f"{branch_now}^{{commit}}")
    if sha_now != task["base"]["sha"]:
        old_sha = task["base"]["sha"]
        store.invalidate_base(
            args.task_id,
            branch=branch_now,
            sha=sha_now,
            reason="승인된 기준 리비전이 변경되었습니다. 계획을 수정하고 다시 승인해야 합니다",
        )
        raise SCVError(
            f"기준 리비전이 {old_sha}에서 {sha_now}(으)로 변경되었습니다. "
            "태스크를 BLOCKED로 전환했으며 PLANNING부터 재개해야 합니다"
        )

    if current is State.BASE_REVALIDATION:
        task = store.transition(
            args.task_id,
            State.MATERIALIZING_WORKTREE,
            note="승인된 기준 리비전을 다시 확인했습니다",
        )

    path = Path(args.worktree).expanduser().resolve() if args.worktree else default_worktree(repo, args.task_id)
    branch = args.branch or f"scv/{args.task_id}"
    validate_branch(repo, branch)
    records = parse_worktrees(repo)
    existing = next((item for item in records if Path(item["worktree"]).resolve() == path), None)
    expected_ref = f"refs/heads/{branch}"
    explicit_adoption = bool(getattr(args, "adopt_existing", False))
    desired_intent = {
        "path": str(path),
        "branch": branch,
        "base_sha": task["base"]["sha"],
    }
    recorded_intent = task.get("artifacts", {}).get("worktree_intent")
    recovering = isinstance(recorded_intent, dict) and all(
        recorded_intent.get(key) == value for key, value in desired_intent.items()
    )
    try:
        if recorded_intent is not None and not recovering:
            raise SCVError("기록된 워크트리 생성 의도가 현재 경로·브랜치·기준과 일치하지 않습니다")
        if existing:
            if not recovering and not explicit_adoption:
                raise SCVError(
                    f"기존 워크트리 {path}에는 이 태스크가 기록한 생성 의도가 없어 채택할 수 없습니다"
                )
            if existing.get("branch") != expected_ref:
                raise SCVError(
                    f"워크트리 {path}의 브랜치는 "
                    f"{existing.get('branch', '분리된 HEAD')}이며, 필요한 브랜치는 {expected_ref}입니다"
                )
            existing_head = git(path, "rev-parse", "HEAD")
            if existing_head != task["base"]["sha"]:
                raise SCVError(
                    f"기존 워크트리 HEAD {existing_head}가 승인된 기준 {task['base']['sha']}와 다릅니다"
                )
            if git(path, "status", "--porcelain", "--untracked-files=all"):
                raise SCVError(f"기존 워크트리 {path}에 커밋되지 않은 변경이 있어 채택할 수 없습니다")
            if not recovering:
                store.set_artifact(
                    args.task_id,
                    "worktree_intent",
                    {**desired_intent, "recorded_at": utc_now(), "adopted_existing": True},
                )
        else:
            if explicit_adoption:
                raise SCVError(f"명시적으로 채택할 기존 워크트리가 없습니다: {path}")
            containing = next(
                (
                    Path(item["worktree"]).resolve()
                    for item in records
                    if path.is_relative_to(Path(item["worktree"]).resolve())
                ),
                None,
            )
            if containing is not None:
                raise SCVError(f"새 워크트리 경로는 기존 워크트리 {containing} 밖에 있어야 합니다")
            if path.exists() and any(path.iterdir()):
                raise SCVError(f"워크트리 경로가 비어 있지 않으며 SCV 관리 대상도 아닙니다: {path}")
            branch_exists = bool(git(repo, "show-ref", "--verify", expected_ref, check=False))
            if branch_exists and not recovering:
                raise SCVError(f"요청한 워크트리 없이 브랜치가 이미 존재합니다: {branch}")
            if not recovering:
                store.set_artifact(
                    args.task_id,
                    "worktree_intent",
                    {**desired_intent, "recorded_at": utc_now()},
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            if branch_exists:
                branch_head = git(repo, "rev-parse", expected_ref)
                if branch_head != task["base"]["sha"]:
                    raise SCVError(
                        f"복구할 브랜치 {branch}가 승인된 기준 리비전을 가리키지 않습니다"
                    )
                git(repo, "worktree", "add", str(path), branch)
            else:
                result = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repo),
                        "worktree",
                        "add",
                        "-b",
                        branch,
                        str(path),
                        task["base"]["sha"],
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                if result.returncode != 0:
                    raise SCVError(result.stderr.strip() or result.stdout.strip())
        materialized_head = git(path, "rev-parse", "HEAD")
        if materialized_head != task["base"]["sha"]:
            raise SCVError("생성된 워크트리 HEAD가 승인된 기준 리비전과 일치하지 않습니다")
        if git(path, "status", "--porcelain", "--untracked-files=all"):
            raise SCVError("생성된 워크트리가 깨끗하지 않아 실행을 시작할 수 없습니다")
        store.set_worktree(args.task_id, path=str(path), branch=branch)
        return store.transition(
            args.task_id,
            State.EXECUTING,
            note="계획 승인 후 격리 워크트리를 생성했습니다",
        )
    except Exception as exc:
        latest = store.load(args.task_id)
        if state_name(latest) == State.MATERIALIZING_WORKTREE.value:
            store.block(args.task_id, reason=safe_reason(f"워크트리 생성 실패: {exc}"))
        raise


def command_execute(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    with controller_execution_lease(store.task_dir(args.task_id)):
        return _command_execute(args, store, repo)


def _command_execute(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    task = store.load(args.task_id)
    require_state(task, (State.EXECUTING,))
    try:
        plan_metadata = approved_artifact(store, task, "plan", "plan.json")
    except SCVError as exc:
        store.block(args.task_id, reason=safe_reason(exc), resume_from=State.PLANNING)
        raise SCVError(f"{exc}. 태스크를 BLOCKED로 전환했습니다") from exc
    worktree = task.get("worktree", {})
    root = Path(worktree.get("path", ""))
    if not root.is_dir():
        raise SCVError(f"기록된 워크트리를 사용할 수 없습니다: {root}")
    plan = artifact_path(store, args.task_id, "plan.json")
    run_relative = Path("runs") / plan_metadata["sha256"]
    run_dir = store.task_dir(args.task_id) / run_relative
    command = [
        sys.executable,
        str(SCRIPT_DIR / "execute.py"),
        str(plan),
        "--root",
        str(root),
        "--run-dir",
        str(run_dir),
        "--learning-root",
        str(store.state_root.parent / "learning"),
        "--expected-base",
        task["base"]["sha"],
        "--revalidate-ready",
    ]
    if args.timeout is not None:
        command.extend(["--timeout", str(args.timeout)])
    try:
        result = subprocess.run(command, check=False)
    except KeyboardInterrupt:
        store.block(args.task_id, reason="사용자가 실행을 중단했습니다")
        raise SCVError("실행이 중단되어 태스크를 BLOCKED로 전환했습니다")
    if result.returncode == EXECUTION_BUSY_EXIT_CODE:
        raise SCVError(
            "다른 SCV 실행기가 같은 실행 증거를 사용 중입니다. 현재 태스크 상태는 변경하지 않았습니다"
        )
    if result.returncode != 0:
        attempts_exhausted = False
        failed_evidence: dict[str, Any] | None = None
        proposal_id: str | None = None
        failure_reason = f"실행기가 종료 코드 {result.returncode}로 실패했습니다"
        index = run_dir / "index.json"
        if index.is_file():
            try:
                with locked_status(run_dir) as loaded_evidence:
                    failed_evidence = json.loads(json.dumps(loaded_evidence))
                    attempts_exhausted = any(
                        isinstance(step, dict)
                        and step.get("status") == "failed"
                        and len(step.get("attempts", [])) >= 3
                        for step in failed_evidence.get("steps", [])
                    )
                    failure_reason = failed_evidence.get("reason") or failure_reason
                    for step in failed_evidence.get("steps", []):
                        if not isinstance(step, dict):
                            continue
                        for attempt in reversed(step.get("attempts", [])):
                            learning = (
                                attempt.get("learning")
                                if isinstance(attempt, dict)
                                else None
                            )
                            if isinstance(learning, dict) and isinstance(
                                learning.get("proposal_id"), str
                            ):
                                proposal_id = learning["proposal_id"]
                                break
                        if proposal_id is not None:
                            break
            except (ExecutionBusy, ExecutorError, OSError, ValueError):
                pass
        failure_artifact = {
            "plan_sha256": plan_metadata["sha256"],
            "attempts_exhausted": attempts_exhausted,
            "reason": safe_reason(failure_reason),
            "failed_at": utc_now(),
        }
        if proposal_id is not None:
            failure_artifact["improvement_proposal_id"] = proposal_id
        store.set_artifact(
            args.task_id,
            "execution_failure",
            failure_artifact,
        )
        store.block(
            args.task_id,
            reason=safe_reason(failure_reason),
            resume_from=State.PLANNING if attempts_exhausted else State.EXECUTING,
        )
        raise SCVError(f"실행기가 종료 코드 {result.returncode}로 실패하여 태스크를 BLOCKED로 전환했습니다")
    try:
        with locked_status(run_dir) as evidence:
            if evidence.get("status") != "ready":
                store.block(args.task_id, reason="실행 증거 상태가 ready가 아닙니다")
                raise SCVError(
                    "실행기가 ready 상태에 도달하지 못해 태스크를 BLOCKED로 전환했습니다"
                )
            verified_workspace = evidence.get("workspace_sha256")
            current_workspace = workspace_fingerprint(root)
            if verified_workspace != current_workspace:
                store.block(
                    args.task_id,
                    reason="실행기 검증 이후 워크트리 내용이 변경되었습니다",
                    resume_from=State.EXECUTING,
                )
                raise SCVError(
                    "실행기 검증 이후 워크트리가 변경되어 태스크를 BLOCKED로 전환했습니다"
                )
            index_path = run_dir / "index.json"
            index_sha256 = hashlib.sha256(index_path.read_bytes()).hexdigest()
            store.set_artifact(
                args.task_id,
                "execution",
                {
                    "path": str(run_relative / "index.json"),
                    "plan_sha256": plan_metadata["sha256"],
                    "index_sha256": index_sha256,
                    "workspace_sha256": verified_workspace,
                    "completed_at": utc_now(),
                },
            )
            return store.transition(
                args.task_id,
                State.HANDOFF,
                note="모든 실행 증거가 검증을 통과했습니다",
            )
    except ExecutionBusy as exc:
        raise SCVError(
            "다른 SCV 실행기가 실행 증거를 갱신 중입니다. 현재 태스크 상태는 변경하지 않았습니다"
        ) from exc
    except (ExecutorError, OSError, ValueError) as exc:
        store.block(
            args.task_id,
            reason=safe_reason(f"실행 증거가 올바르지 않습니다: {exc}"),
        )
        raise SCVError("실행 증거가 올바르지 않아 태스크를 BLOCKED로 전환했습니다") from exc


def command_handoff(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    with controller_execution_lease(store.task_dir(args.task_id)):
        return _command_handoff(args, store, repo)


def _command_handoff(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    task = store.load(args.task_id)
    require_state(task, (State.HANDOFF,))
    try:
        plan_metadata = approved_artifact(store, task, "plan", "plan.json")
    except SCVError as exc:
        store.block(args.task_id, reason=safe_reason(exc), resume_from=State.PLANNING)
        raise SCVError(f"{exc}. 태스크를 BLOCKED로 전환했습니다") from exc
    root = Path(task.get("worktree", {}).get("path", ""))
    if not root.is_dir():
        raise SCVError(f"기록된 워크트리를 사용할 수 없습니다: {root}")
    execution = task.get("artifacts", {}).get("execution")
    if not isinstance(execution, dict) or not execution.get("path"):
        store.block(args.task_id, reason="실행 증거 경로가 없습니다", resume_from=State.EXECUTING)
        raise SCVError("실행 증거 경로가 없어 태스크를 BLOCKED로 전환했습니다")
    expected_index = Path("runs") / plan_metadata["sha256"] / "index.json"
    if (
        execution.get("path") != str(expected_index)
        or execution.get("plan_sha256") != plan_metadata["sha256"]
    ):
        store.block(
            args.task_id,
            reason="실행 증거가 승인된 계획과 연결되지 않습니다",
            resume_from=State.EXECUTING,
        )
        raise SCVError("실행 증거 경로가 승인된 계획과 달라 태스크를 BLOCKED로 전환했습니다")
    run_dir = store.task_dir(args.task_id) / expected_index.parent
    try:
        with locked_status(run_dir) as evidence:
            try:
                require_execution_index_fingerprint(execution, run_dir)
            except SCVError as exc:
                store.block(
                    args.task_id,
                    reason=safe_reason(exc),
                    resume_from=State.EXECUTING,
                )
                raise SCVError(
                    "실행 인덱스가 변경되어 태스크를 BLOCKED로 전환했습니다"
                ) from exc
            bindings = {
                "task_id": args.task_id,
                "plan_sha256": plan_metadata["sha256"],
                "expected_base_sha": task["base"]["sha"],
                "workspace": str(root.resolve()),
                "workspace_sha256": execution.get("workspace_sha256"),
            }
            if any(evidence.get(name) != value for name, value in bindings.items()):
                store.block(
                    args.task_id,
                    reason="실행 증거의 태스크·계획·기준·워크트리 연결이 일치하지 않습니다",
                    resume_from=State.EXECUTING,
                )
                raise SCVError(
                    "실행 증거가 현재 태스크와 연결되지 않아 태스크를 BLOCKED로 전환했습니다"
                )
            if evidence.get("status") != "ready":
                store.block(
                    args.task_id,
                    reason="인계할 실행 증거 상태가 ready가 아닙니다",
                    resume_from=State.EXECUTING,
                )
                raise SCVError("실행 증거가 ready 상태가 아니어서 태스크를 BLOCKED로 전환했습니다")
            return _record_handoff(
                args=args,
                store=store,
                task=task,
                root=root,
                run_dir=run_dir,
                evidence=evidence,
                execution=execution,
            )
    except ExecutionBusy as exc:
        raise SCVError(
            "다른 SCV 실행기가 실행 증거를 갱신 중입니다. 현재 태스크 상태는 변경하지 않았습니다"
        ) from exc
    except (ExecutorError, OSError, ValueError) as exc:
        store.block(
            args.task_id,
            reason=safe_reason(f"인계 전 실행 증거 검증에 실패했습니다: {exc}"),
            resume_from=State.EXECUTING,
        )
        raise SCVError("실행 증거가 올바르지 않아 태스크를 BLOCKED로 전환했습니다") from exc


def _record_handoff(
    *,
    args: argparse.Namespace,
    store: TaskStateStore,
    task: dict[str, Any],
    root: Path,
    run_dir: Path,
    evidence: dict[str, Any],
    execution: dict[str, Any],
) -> dict[str, Any]:
    head = git(root, "rev-parse", "HEAD")
    if head.lower() != task["base"]["sha"].lower():
        store.block(
            args.task_id,
            reason="실행 검증 이후 워크트리 HEAD가 승인된 기준에서 변경되었습니다",
            resume_from=State.EXECUTING,
        )
        raise SCVError(
            "실행 검증 이후 워크트리 HEAD가 변경되어 태스크를 BLOCKED로 전환했습니다"
        )
    current_workspace = workspace_fingerprint(root)
    if execution.get("workspace_sha256") != current_workspace:
        store.block(
            args.task_id,
            reason="실행 검증 이후 워크트리 내용이 변경되었습니다",
            resume_from=State.EXECUTING,
        )
        raise SCVError("실행 검증 이후 워크트리가 변경되어 태스크를 BLOCKED로 전환했습니다")
    status = git(root, "status", "--short")
    changed = git(root, "diff", "--name-only", task["base"]["sha"])
    untracked = git(root, "ls-files", "--others", "--exclude-standard")
    changed_paths = sorted(
        {line for line in (changed + "\n" + untracked).splitlines() if line.strip()}
    )
    diff_stat = git(root, "diff", "--stat", task["base"]["sha"])
    verification_lines = []
    reported_risks: list[str] = []
    for step in evidence.get("steps", []):
        attempts = step.get("attempts", []) if isinstance(step, dict) else []
        verification_lines.append(
            f"- {step.get('id', '알 수 없음')}: {step.get('status', '알 수 없음')} "
            f"(시도 {len(attempts)}회)"
        )
        for attempt in reversed(attempts):
            if attempt.get("status") != "passed" or not attempt.get("evidence"):
                continue
            worker_result = run_dir / attempt["evidence"] / "worker-final.json"
            try:
                worker_evidence = json.loads(worker_result.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                break
            reported_risks.extend(
                risk for risk in worker_evidence.get("risks", []) if isinstance(risk, str) and risk.strip()
            )
            break
    final_acceptance = evidence.get("final_acceptance")
    if isinstance(final_acceptance, dict):
        verification_lines.append(
            f"- 최종 검증: {final_acceptance.get('status', '알 수 없음')}"
        )
    lines = [
        f"# SCV 인계 보고서: {args.task_id}",
        "",
        f"- 상태: READY",
        f"- 목표: {task['target']}",
        f"- 기준: `{task['base']['branch']}`의 `{task['base']['sha']}`",
        f"- 워크트리 HEAD: `{head}`",
        f"- 브랜치: `{task['worktree']['branch']}`",
        f"- 워크트리: `{root}`",
        f"- 실행기 상태: `{evidence.get('status')}`",
        "",
        "## 변경 파일",
        "",
        "```text",
        "\n".join(changed_paths) or "(없음)",
        "```",
        "",
        "## 변경 요약",
        "",
        "```text",
        diff_stat or "(변경 없음)",
        "```",
        "",
        "## 워크트리 상태",
        "",
        "```text",
        status or "깨끗함",
        "```",
        "",
        "## 검증 결과",
        "",
        "\n".join(verification_lines) or "- 단계별 증거를 찾을 수 없습니다.",
        "",
        "## 보고된 위험",
        "",
        "\n".join(f"- {risk}" for risk in sorted(set(reported_risks)))
        or "- 통과한 작업 시도에서 보고된 위험이 없습니다.",
        "",
        "워크트리는 의도적으로 보존했습니다. 병합, push, 게시, 정리는 별도 승인이 필요합니다.",
        "",
    ]
    path = artifact_path(store, args.task_id, "handoff.md")
    atomic_write(path, "\n".join(lines).encode())
    value = fingerprint(path, store.task_dir(args.task_id))
    return store.record_artifact(
        args.task_id,
        "handoff",
        value,
        transition_to=State.READY,
        note="인계 증거를 기록했으며 워크트리는 보존했습니다",
    )


def command_resume(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    task = store.load(args.task_id)
    if task.get("target") == "plan" and state_name(task) == State.READY.value:
        # A plan-only task becomes a full task at this transition. Prove the
        # nested Codex and Seatbelt runtime before mutating its durable target.
        preflight_start_runtime(repo)
    return store.resume(args.task_id, note="SCV 워크플로를 재개했습니다")


def command_abandon(args: argparse.Namespace, store: TaskStateStore, repo: Path) -> dict[str, Any]:
    return store.abandon(args.task_id, reason=args.reason)


def build_parser() -> argparse.ArgumentParser:
    localize_argparse()
    parser = argparse.ArgumentParser(description="Codex SCV 워크플로 제어기를 실행합니다")
    parser.add_argument(
        "--repo", default=".", metavar="저장소", help="Git 저장소 또는 워크트리 경로"
    )
    parser.add_argument(
        "--state-root", metavar="상태-루트", help="태스크 상태 루트 재정의(주로 테스트용)"
    )
    subparsers = parser.add_subparsers(dest="command")

    start = subparsers.add_parser("start", help="새 요구사항 접수를 시작합니다")
    start.add_argument("target", metavar="목표", choices=("analyze", "plan", "full"))
    start.add_argument("--task-id", required=True, metavar="태스크-ID", help="태스크 식별자")
    request = start.add_mutually_exclusive_group(required=True)
    request.add_argument("--request", metavar="요구사항", help="접수할 요구사항 본문")
    request.add_argument("--request-file", metavar="파일", help="요구사항을 읽을 파일 경로")
    start.add_argument("--base", metavar="기준", help="기준 브랜치 또는 리비전")

    command_help = {
        "status": "태스크의 현재 상태를 표시합니다",
        "approve-spec": "제출된 스펙을 승인합니다",
        "approve-plan": "제출된 구현 계획을 승인합니다",
        "execute": "승인된 계획을 실행합니다",
        "handoff": "검증 결과와 변경 내용을 인계 보고서로 정리합니다",
        "resume": "차단된 태스크를 재개하거나 목표를 승격합니다",
    }
    for name in ("status", "approve-spec", "approve-plan", "execute", "handoff", "resume"):
        command = subparsers.add_parser(name, help=command_help[name])
        command.add_argument("task_id", metavar="태스크-ID")
        if name == "execute":
            command.add_argument("--timeout", type=int, metavar="초", help="명령별 제한 시간(초)")

    spec = subparsers.add_parser("submit-spec", help="스펙을 승인 대기 상태로 제출합니다")
    spec.add_argument("task_id", metavar="태스크-ID")
    spec.add_argument("--spec", required=True, metavar="스펙-파일", help="제출할 스펙 파일 경로")

    plan = subparsers.add_parser("submit-plan", help="구현 계획을 승인 대기 상태로 제출합니다")
    plan.add_argument("task_id", metavar="태스크-ID")
    plan.add_argument("--plan", required=True, metavar="계획-파일", help="제출할 계획 JSON 경로")

    materialize = subparsers.add_parser("materialize", help="승인 후 격리 워크트리를 생성합니다")
    materialize.add_argument("task_id", metavar="태스크-ID")
    materialize.add_argument("--worktree", metavar="워크트리", help="생성하거나 채택할 워크트리 경로")
    materialize.add_argument("--branch", metavar="브랜치", help="생성하거나 채택할 작업 브랜치")
    materialize.add_argument(
        "--adopt-existing",
        action="store_true",
        help="사용자가 지정한 기존 워크트리를 엄격히 확인한 뒤 채택합니다",
    )

    abandon = subparsers.add_parser("abandon", help="태스크를 포기 상태로 기록합니다")
    abandon.add_argument("task_id", metavar="태스크-ID")
    abandon.add_argument("--reason", metavar="사유", help="포기 사유")
    return parser


COMMANDS = {
    "start": command_start,
    "status": command_status,
    "submit-spec": command_submit_spec,
    "approve-spec": command_approve_spec,
    "submit-plan": command_submit_plan,
    "approve-plan": command_approve_plan,
    "materialize": command_materialize,
    "execute": command_execute,
    "handoff": command_handoff,
    "resume": command_resume,
    "abandon": command_abandon,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("명령을 지정해야 합니다")
    try:
        require_macos()
        repo = resolve_repo(args.repo)
        if (
            (args.command == "start" and args.target == "full")
            or args.command in {"materialize", "execute"}
        ):
            preflight_start_runtime(repo)
        store = TaskStateStore(repo=repo, state_root=args.state_root)
        result = COMMANDS[args.command](args, store, repo)
        emit(result)
        return 0
    except (
        SCVError,
        SCVStateError,
        ExecutorError,
        RuntimeRequirementError,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
