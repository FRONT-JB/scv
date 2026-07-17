#!/usr/bin/env python3
"""Human-gated maintenance commands for SCV failure learning."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    from .cli_ko import localize_argparse
    from .scv import (
        SCVError,
        execution_evidence_context,
        git,
        require_execution_index_fingerprint,
        require_ready_execution_bindings,
        resolve_repo,
    )
    from .scv_state import SCVStateError, State, TaskStateStore
    from .execute import ExecutionBusy, ExecutorError, locked_status
    from .learning import LearningError, LearningStore
    from .runtime import RuntimeRequirementError, require_macos
except ImportError:  # pragma: no cover - direct script execution.
    from cli_ko import localize_argparse
    from scv import (
        SCVError,
        execution_evidence_context,
        git,
        require_execution_index_fingerprint,
        require_ready_execution_bindings,
        resolve_repo,
    )
    from scv_state import SCVStateError, State, TaskStateStore
    from execute import ExecutionBusy, ExecutorError, locked_status
    from learning import LearningError, LearningStore
    from runtime import RuntimeRequirementError, require_macos


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _learning_store(store: TaskStateStore) -> LearningStore:
    return LearningStore(store.state_root.parent / "learning")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _require_repair_source_checkout(repair_repo: Path) -> Path:
    """Return the tracked SCV source root and reject installed plugin caches."""

    source_root = (repair_repo / "plugins" / "codex" / "scv").resolve()
    if not _is_within(source_root, repair_repo.resolve()):
        raise SCVError("SCV 수리 경로가 지정한 Git 저장소 밖을 가리킵니다")

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    installed_plugins = (codex_home.expanduser().resolve() / "plugins").resolve()
    if _is_within(source_root, installed_plugins):
        raise SCVError("설치된 Codex 플러그인 영역은 SCV 소스 수리 저장소로 사용할 수 없습니다")

    required = (
        Path(".codex-plugin/plugin.json"),
        Path("scripts/execute.py"),
        Path("scripts/improve.py"),
        Path("skills/workflow/SKILL.md"),
        Path("skills/improve/SKILL.md"),
    )
    for relative in required:
        candidate = source_root / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise SCVError(f"SCV 소스 파일이 없거나 심볼릭 링크입니다: {candidate}")
        repository_relative = candidate.relative_to(repair_repo).as_posix()
        try:
            git(repair_repo, "ls-files", "--error-unmatch", "--", repository_relative)
        except SCVError as exc:
            raise SCVError(
                f"SCV 소스 파일이 Git 추적 대상이 아닙니다: {candidate}"
            ) from exc

    manifest_path = source_root / ".codex-plugin" / "plugin.json"
    if manifest_path.stat().st_size > 65_536:
        raise SCVError("SCV 플러그인 매니페스트가 허용 크기를 초과했습니다")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SCVError(f"SCV 플러그인 매니페스트를 읽을 수 없습니다: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("name") != "scv":
        raise SCVError("수리 저장소의 플러그인 매니페스트가 SCV 소스를 가리키지 않습니다")
    return source_root


def _verify_controller_proposal(
    store: TaskStateStore,
    learning: LearningStore,
    proposal_id: str,
) -> dict[str, Any]:
    proposal = learning.validate_proposal_source(proposal_id)
    observation = learning.load_observation(proposal["source_observation_id"])
    failure = observation["failure"]
    store.load(proposal["task_id"])
    plan_sha256 = failure.get("plan_sha256")
    if not isinstance(plan_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", plan_sha256) is None:
        raise SCVError("SCV 개선 제안의 원본 계획 SHA-256이 올바르지 않습니다")
    run_dir = store.task_dir(proposal["task_id"]) / "runs" / plan_sha256
    with locked_status(run_dir) as evidence:
        if (
            evidence.get("task_id") != proposal["task_id"]
            or evidence.get("plan_sha256") != plan_sha256
        ):
            raise SCVError("SCV 개선 제안의 실행 인덱스 연결이 일치하지 않습니다")
        source_found = False
        for step in evidence.get("steps", []):
            if not isinstance(step, dict) or step.get("id") != failure.get("step_id"):
                continue
            for attempt in step.get("attempts", []):
                if not isinstance(attempt, dict):
                    continue
                attempt_learning = attempt.get("learning")
                if not isinstance(attempt_learning, dict):
                    continue
                if (
                    attempt.get("number") == failure.get("attempt_number")
                    and attempt.get("status") == "failed"
                    and attempt.get("evidence_sha256")
                    == failure.get("evidence_sha256")
                    and attempt.get("failure", {}).get("stage")
                    == failure.get("stage")
                    and attempt_learning.get("status") == "analyzed"
                    and attempt_learning.get("signature")
                    == failure.get("signature")
                    and attempt_learning.get("observation_id")
                    == observation["observation_id"]
                    and attempt_learning.get("analysis_evidence_sha256")
                    == observation.get("analyst_evidence_sha256")
                    and attempt_learning.get("proposal_id") == proposal_id
                ):
                    source_found = True
                    break
            if source_found:
                break
        if not source_found:
            raise SCVError("SCV 개선 제안이 실제 실패·분석 증거와 연결되지 않습니다")
    return proposal


def command_list(
    args: argparse.Namespace,
    store: TaskStateStore,
    learning: LearningStore,
) -> dict[str, Any]:
    task_id = args.task_id
    if task_id is not None:
        store.load(task_id)
    proposals = learning.list_proposals(task_id=task_id)
    presented: list[dict[str, Any]] = []
    for proposal in proposals:
        item = dict(proposal)
        try:
            _verify_controller_proposal(store, learning, proposal["proposal_id"])
            item["evidence_status"] = "verified"
        except (
            SCVError,
            ExecutorError,
            ExecutionBusy,
            LearningError,
            OSError,
            ValueError,
        ) as exc:
            item["evidence_status"] = "invalid"
            item["evidence_error"] = str(exc)
        presented.append(item)
    return {
        "task_id": task_id,
        "lessons": learning.list_lessons(task_id=task_id),
        "proposals": presented,
    }


def command_approve(
    args: argparse.Namespace,
    store: TaskStateStore,
    learning: LearningStore,
) -> dict[str, Any]:
    task = store.load(args.task_id)
    if task.get("target") != "full" or task.get("state") != State.READY.value:
        raise SCVError("lesson 승인은 완료된 full 태스크의 READY 상태에서만 가능합니다")
    lesson = learning.load_lesson(args.lesson_id)
    if lesson.get("status") != "candidate":
        raise SCVError("승인할 lesson은 candidate 상태여야 합니다")
    if lesson.get("task_id") != args.task_id:
        raise SCVError("lesson이 지정한 태스크에서 생성되지 않았습니다")
    source_observation_id = lesson.get("source_observation_id")
    if not isinstance(source_observation_id, str):
        raise SCVError("lesson의 원본 관찰 참조가 올바르지 않습니다")
    observation = learning.load_observation(source_observation_id)
    failure = observation.get("failure")
    if not isinstance(failure, dict):
        raise SCVError("lesson의 원본 실패 관찰이 올바르지 않습니다")

    plan_metadata, execution, root, run_dir = execution_evidence_context(
        store, args.task_id, task
    )
    if (
        failure.get("task_id") != args.task_id
        or failure.get("plan_sha256") != plan_metadata.get("sha256")
    ):
        raise SCVError("lesson의 원본 관찰이 현재 태스크·계획과 일치하지 않습니다")
    try:
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
            final_validation = evidence.get("final_validation")
            final_sha256 = (
                final_validation.get("evidence_sha256")
                if isinstance(final_validation, dict)
                else None
            )
            if not isinstance(final_sha256, str):
                raise SCVError("lesson 승인에 필요한 최종 검증 증거가 없습니다")
            successful_sha256 = lesson.get("validation", {}).get(
                "successful_evidence_sha256"
            )
            source_found = False
            resolution_found = False
            for step in evidence.get("steps", []):
                if not isinstance(step, dict):
                    continue
                for attempt in step.get("attempts", []):
                    if not isinstance(attempt, dict):
                        continue
                    attempt_learning = attempt.get("learning")
                    if not isinstance(attempt_learning, dict):
                        continue
                    if attempt_learning.get("observation_id") == source_observation_id:
                        source_found = source_found or (
                            step.get("id") == failure.get("step_id")
                            and attempt.get("number") == failure.get("attempt_number")
                            and attempt.get("status") == "failed"
                            and attempt.get("evidence_sha256")
                            == failure.get("evidence_sha256")
                            and attempt.get("failure", {}).get("stage")
                            == failure.get("stage")
                            and attempt_learning.get("status") == "analyzed"
                            and attempt_learning.get("signature")
                            == failure.get("signature")
                            and attempt_learning.get("analysis_evidence_sha256")
                            == observation.get("analyst_evidence_sha256")
                        )
                    if (
                        attempt_learning.get("candidate_lesson_id")
                        == args.lesson_id
                        and attempt_learning.get("status") == "candidate-created"
                        and attempt_learning.get("source_observation_id")
                        == source_observation_id
                        and step.get("id") == failure.get("step_id")
                        and isinstance(attempt.get("number"), int)
                        and isinstance(failure.get("attempt_number"), int)
                        and attempt["number"] > failure["attempt_number"]
                        and attempt.get("status") == "passed"
                        and attempt.get("evidence_sha256") == successful_sha256
                    ):
                        resolution_found = True
            if not source_found or not resolution_found:
                raise SCVError(
                    "candidate lesson이 현재 실행의 실패·성공 증거와 연결되지 않습니다"
                )
    except ExecutionBusy as exc:
        raise SCVError("다른 SCV 실행기가 실행 증거를 갱신 중입니다") from exc
    except (ExecutorError, OSError, ValueError) as exc:
        raise SCVError(f"lesson 승인 전 실행 증거 검증에 실패했습니다: {exc}") from exc

    return learning.approve(
        args.lesson_id,
        approval_evidence={
            "execution_index_sha256": execution["index_sha256"],
            "final_evidence_sha256": final_sha256,
        },
    )


def command_retire(
    args: argparse.Namespace,
    store: TaskStateStore,
    learning: LearningStore,
) -> dict[str, Any]:
    del store
    return learning.retire(args.lesson_id)


def command_proposal_handoff(
    args: argparse.Namespace,
    store: TaskStateStore,
    learning: LearningStore,
) -> dict[str, Any]:
    proposal = _verify_controller_proposal(store, learning, args.proposal_id)
    if args.repair_task_id == proposal.get("task_id"):
        raise SCVError("SCV 개선 제안은 원본 태스크와 다른 수리 태스크로 인계해야 합니다")
    repair_repo = resolve_repo(args.repair_repo)
    repair_source_root = _require_repair_source_checkout(repair_repo)
    repair_store = TaskStateStore(repo=repair_repo)
    repair_task = repair_store.load(args.repair_task_id)
    request_text = repair_task.get("artifacts", {}).get("request", {}).get("text")
    if repair_task.get("target") != "full":
        raise SCVError("SCV 수리 태스크는 full 목표여야 합니다")
    if repair_task.get("state") == State.ABANDONED.value:
        raise SCVError("포기된 태스크로 SCV 개선 제안을 인계할 수 없습니다")
    exact_proposal = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(args.proposal_id)}(?![A-Za-z0-9])"
    )
    if not isinstance(request_text, str) or exact_proposal.search(request_text) is None:
        raise SCVError("SCV 수리 태스크의 요청에 원본 개선 제안 ID가 없습니다")
    return learning.handoff_proposal(
        args.proposal_id,
        repair_repo=str(repair_repo),
        repair_plugin_root=str(repair_source_root),
        repair_task_id=args.repair_task_id,
    )


def command_proposal_close(
    args: argparse.Namespace,
    store: TaskStateStore,
    learning: LearningStore,
) -> dict[str, Any]:
    del store
    return learning.close_proposal(args.proposal_id, reason=args.reason)


def build_parser() -> argparse.ArgumentParser:
    localize_argparse()
    parser = argparse.ArgumentParser(
        description="SCV 실패 학습 후보와 개선 제안을 사람 승인으로 관리합니다"
    )
    parser.add_argument(
        "--repo", default=".", metavar="저장소", help="Git 저장소 또는 워크트리 경로"
    )
    parser.add_argument(
        "--state-root", metavar="상태-루트", help="태스크 상태 루트 재정의(주로 테스트용)"
    )
    subparsers = parser.add_subparsers(dest="command")

    listing = subparsers.add_parser(
        "list", help="lesson 후보와 SCV 개선 제안을 표시합니다"
    )
    listing.add_argument("--task-id", metavar="태스크-ID")

    approve = subparsers.add_parser(
        "approve", help="최종 실행 증거를 확인한 candidate lesson을 활성화합니다"
    )
    approve.add_argument("task_id", metavar="태스크-ID")
    approve.add_argument("lesson_id", metavar="LESSON-ID")

    retire = subparsers.add_parser(
        "retire", help="더 이상 사용하지 않을 lesson을 폐기합니다"
    )
    retire.add_argument("lesson_id", metavar="LESSON-ID")

    handoff = subparsers.add_parser(
        "proposal-handoff", help="검증된 SCV 개선 제안을 별도 full 수리 태스크에 연결합니다"
    )
    handoff.add_argument("proposal_id", metavar="제안-ID")
    handoff.add_argument("--repair-repo", required=True, metavar="수리-저장소")
    handoff.add_argument("--repair-task-id", required=True, metavar="수리-태스크-ID")

    close = subparsers.add_parser(
        "proposal-close", help="처리하지 않거나 완료한 SCV 개선 제안을 종료합니다"
    )
    close.add_argument("proposal_id", metavar="제안-ID")
    close.add_argument("--reason", required=True, metavar="종료-사유")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("명령을 지정해야 합니다")
    try:
        require_macos()
        repo = resolve_repo(args.repo)
        store = TaskStateStore(repo=repo, state_root=args.state_root)
        learning = _learning_store(store)
        commands = {
            "list": command_list,
            "approve": command_approve,
            "retire": command_retire,
            "proposal-handoff": command_proposal_handoff,
            "proposal-close": command_proposal_close,
        }
        _emit(commands[args.command](args, store, learning))
        return 0
    except (
        SCVError,
        SCVStateError,
        ExecutorError,
        LearningError,
        RuntimeRequirementError,
        OSError,
        ValueError,
        KeyError,
    ) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
