#!/usr/bin/env python3
"""Tests for the Codex SCV task state store."""

from __future__ import annotations

import io
import json
import multiprocessing
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import scv_state  # noqa: E402
from scv_state import (  # noqa: E402
    CorruptState,
    InvalidTaskId,
    InvalidTransition,
    State,
    TaskExists,
    TaskStateStore,
)


def _concurrent_create_worker(repo, state_root, task_id, base_branch, base_sha, start, results):
    start.wait(10)
    store = TaskStateStore(repo=repo, state_root=state_root)
    try:
        record = store.create(
            task_id,
            target="full",
            base_branch=base_branch,
            base_sha=base_sha,
        )
        results.put(("생성", record["revision"]))
    except TaskExists:
        results.put(("중복", None))
    except BaseException as exc:  # pragma: no cover - reported to the parent.
        results.put(("오류", "{}: {}".format(type(exc).__name__, exc)))


def _blocking_artifact_worker(
    repo,
    state_root,
    task_id,
    entered_clock,
    release_clock,
    results,
):
    def clock():
        entered_clock.set()
        if not release_clock.wait(10):
            raise RuntimeError("첫 번째 갱신 대기 시간이 초과되었습니다")
        return "2026-07-13T00:00:01.000Z"

    store = TaskStateStore(repo=repo, state_root=state_root, clock=clock)
    try:
        record = store.set_artifact(task_id, "first", {"value": 1})
        results.put(("첫 번째", record["revision"]))
    except BaseException as exc:  # pragma: no cover - reported to the parent.
        results.put(("오류", "{}: {}".format(type(exc).__name__, exc)))


def _observed_artifact_worker(
    repo,
    state_root,
    task_id,
    attempting,
    entered_clock,
    results,
):
    def clock():
        entered_clock.set()
        return "2026-07-13T00:00:02.000Z"

    store = TaskStateStore(repo=repo, state_root=state_root, clock=clock)
    attempting.set()
    try:
        record = store.set_artifact(task_id, "second", {"value": 2})
        results.put(("두 번째", record["revision"]))
    except BaseException as exc:  # pragma: no cover - reported to the parent.
        results.put(("오류", "{}: {}".format(type(exc).__name__, exc)))


class TaskStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        (self.repo / "README.md").write_text("scv state tests\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git(
            "-c",
            "user.name=SCV Tests",
            "-c",
            "user.email=scv-tests@example.invalid",
            "commit",
            "-q",
            "-m",
            "initial",
        )
        self.sha = self._git("rev-parse", "HEAD").stdout.strip()
        self.branch = self._git("branch", "--show-current").stdout.strip()
        self.store = TaskStateStore(repo=self.repo)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=str(cwd or self.repo),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _create(self, task_id: str = "task-1", target: str = "full") -> dict:
        return self.store.create(
            task_id,
            target=target,
            base_branch=self.branch,
            base_sha=self.sha,
        )

    def _join_process(self, process) -> None:
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
            self.fail("동시성 테스트 프로세스가 종료되지 않았습니다")
        self.assertEqual(process.exitcode, 0)

    def _to_spec_approval(self, task_id: str, target: str) -> dict:
        self._create(task_id, target)
        self.store.transition(task_id, State.INTAKING)
        return self.store.record_artifact(
            task_id,
            "spec",
            {"path": "spec.md", "sha256": "a" * 64},
            transition_to=State.AWAITING_SPEC_APPROVAL,
        )

    def _to_plan_approval(self, task_id: str, target: str) -> dict:
        self._to_spec_approval(task_id, target)
        self.store.transition(task_id, State.PLANNING)
        return self.store.record_artifact(
            task_id,
            "plan",
            {"path": "plan.json", "sha256": "b" * 64},
            transition_to=State.AWAITING_PLAN_APPROVAL,
        )

    def test_default_root_uses_git_common_directory(self) -> None:
        expected = (self.repo / ".git" / "scv" / "tasks").resolve()
        self.assertEqual(self.store.state_root, expected)
        self.assertEqual(self.store.task_dir("safe.1"), expected / "safe.1")

        created = self._create("safe.1")
        self.assertEqual(created["state"], State.NEW.value)
        self.assertTrue((expected / "safe.1" / "state.json").is_file())

    def test_linked_worktree_resolves_same_common_state(self) -> None:
        self._create("shared")
        linked = self.root / "linked"
        self._git("worktree", "add", "-q", "-b", "state-test", str(linked))

        linked_store = TaskStateStore(repo=linked)
        self.assertEqual(linked_store.state_root, self.store.state_root)
        self.assertEqual(linked_store.load("shared")["task_id"], "shared")

    def test_create_persists_required_fields_and_refuses_overwrite(self) -> None:
        created = self._create(artifacts_target := "first-task")
        self.assertEqual(created["target"], "full")
        self.assertEqual(created["base"], {"branch": self.branch, "sha": self.sha})
        self.assertEqual(created["worktree"], {"path": None, "branch": None})
        self.assertEqual(created["artifacts"], {})
        self.assertEqual(created["revision"], 1)
        self.assertEqual(created["history"][0]["event"], "created")
        self.assertIn("created_at", created["timestamps"])

        with self.assertRaises(TaskExists):
            self._create(artifacts_target)

    def test_create_can_publish_directly_as_intaking(self) -> None:
        created = self.store.create(
            "started-atomically",
            target="full",
            base_branch=self.branch,
            base_sha=self.sha,
            artifacts={"request": {"text": "변경 요청"}},
            initial_state=State.INTAKING,
        )

        self.assertEqual(created["state"], State.INTAKING.value)
        self.assertEqual(created["revision"], 1)
        self.assertEqual(len(created["history"]), 1)
        self.assertEqual(created["history"][0]["to"], State.INTAKING.value)
        self.assertEqual(
            set(created["timestamps"]["state_entries"]),
            {State.INTAKING.value},
        )
        self.assertEqual(self.store.load("started-atomically"), created)

        with self.assertRaisesRegex(InvalidTransition, "최초 상태"):
            self.store.create(
                "invalid-initial-state",
                target="full",
                base_branch=self.branch,
                base_sha=self.sha,
                initial_state=State.PLANNING,
            )
        self.assertFalse(self.store.task_dir("invalid-initial-state").exists())

    def test_concurrent_create_has_exactly_one_winner(self) -> None:
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        arguments = (
            str(self.repo),
            str(self.store.state_root),
            "same-task",
            self.branch,
            self.sha,
            start,
            results,
        )
        processes = [
            context.Process(target=_concurrent_create_worker, args=arguments)
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            self._join_process(process)

        outcomes = sorted(results.get(timeout=5)[0] for _ in processes)
        self.assertEqual(outcomes, ["생성", "중복"])
        record = self.store.load("same-task")
        self.assertEqual(record["revision"], 1)
        self.assertEqual(record["history"][0]["event"], "created")

    def test_process_lock_covers_load_mutate_and_replace(self) -> None:
        self._create("concurrent-update")
        context = multiprocessing.get_context("spawn")
        first_entered = context.Event()
        release_first = context.Event()
        second_attempting = context.Event()
        second_entered = context.Event()
        results = context.Queue()
        first = context.Process(
            target=_blocking_artifact_worker,
            args=(
                str(self.repo),
                str(self.store.state_root),
                "concurrent-update",
                first_entered,
                release_first,
                results,
            ),
        )
        second = context.Process(
            target=_observed_artifact_worker,
            args=(
                str(self.repo),
                str(self.store.state_root),
                "concurrent-update",
                second_attempting,
                second_entered,
                results,
            ),
        )

        first.start()
        self.assertTrue(first_entered.wait(10), "첫 번째 갱신이 잠금 구간에 진입하지 못했습니다")
        second.start()
        try:
            self.assertTrue(second_attempting.wait(10), "두 번째 갱신이 시작되지 않았습니다")
            self.assertFalse(
                second_entered.wait(0.5),
                "두 번째 갱신이 첫 번째 원자 갱신 도중 상태를 읽었습니다",
            )
        finally:
            release_first.set()

        self._join_process(first)
        self._join_process(second)
        self.assertTrue(second_entered.wait(5))
        outcomes = sorted(results.get(timeout=5)[0] for _ in range(2))
        self.assertEqual(outcomes, ["두 번째", "첫 번째"])
        record = self.store.load("concurrent-update")
        self.assertEqual(record["revision"], 3)
        self.assertEqual(record["artifacts"]["first"], {"value": 1})
        self.assertEqual(record["artifacts"]["second"], {"value": 2})

    def test_unsafe_task_ids_are_rejected_before_path_use(self) -> None:
        unsafe = ["", ".hidden", "../escape", "a/b", "two words", "x" * 65]
        for task_id in unsafe:
            with self.subTest(task_id=task_id):
                with self.assertRaises(InvalidTaskId):
                    self.store.task_dir(task_id)

    def test_full_target_follows_complete_lifecycle(self) -> None:
        task_id = "full-flow"
        self._to_plan_approval(task_id, "full")
        states = [State.BASE_REVALIDATION, State.MATERIALIZING_WORKTREE]
        for state in states:
            self.store.transition(task_id, state)
        self.store.set_worktree(
            task_id,
            path=self.root / "implementation-worktree",
            branch="scv/full-flow",
        )
        for state in [State.EXECUTING, State.HANDOFF, State.READY]:
            record = self.store.transition(task_id, state)

        self.assertEqual(record["state"], State.READY.value)
        self.assertIsNotNone(record["timestamps"]["ready_at"])
        transitions = [
            item["to"] for item in record["history"] if item["event"] == "transition"
        ]
        self.assertEqual(
            transitions,
            [
                State.INTAKING.value,
                State.AWAITING_SPEC_APPROVAL.value,
                State.PLANNING.value,
                State.AWAITING_PLAN_APPROVAL.value,
                State.BASE_REVALIDATION.value,
                State.MATERIALIZING_WORKTREE.value,
                State.EXECUTING.value,
                State.HANDOFF.value,
                State.READY.value,
            ],
        )

    def test_target_specific_terminal_edges_are_enforced(self) -> None:
        self._to_spec_approval("analyze-ok", "analyze")
        analyze = self.store.transition("analyze-ok", State.READY)
        self.assertEqual(analyze["state"], State.READY.value)

        self._to_spec_approval("analyze-bad", "analyze")
        with self.assertRaisesRegex(InvalidTransition, "계획 단계로 진입"):
            self.store.transition("analyze-bad", State.PLANNING)

        self._to_plan_approval("plan-ok", "plan")
        plan = self.store.transition("plan-ok", State.READY)
        self.assertEqual(plan["state"], State.READY.value)

        self._to_plan_approval("plan-bad", "plan")
        with self.assertRaisesRegex(InvalidTransition, "목표는 full"):
            self.store.transition("plan-bad", State.BASE_REVALIDATION)

    def test_illegal_transition_does_not_change_record(self) -> None:
        original = self._create("out-of-order")
        with self.assertRaises(InvalidTransition):
            self.store.transition("out-of-order", State.EXECUTING)
        current = self.store.load("out-of-order")
        self.assertEqual(current, original)

    def test_artifact_and_transition_are_one_atomic_revision(self) -> None:
        self._create("artifact-task")
        self.store.transition("artifact-task", State.INTAKING)
        before = self.store.load("artifact-task")

        after = self.store.record_artifact(
            "artifact-task",
            "spec",
            {"path": "spec.md", "sha256": "c" * 64},
            transition_to=State.AWAITING_SPEC_APPROVAL,
        )
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertIn("spec", after["artifacts"])
        self.assertEqual(after["state"], State.AWAITING_SPEC_APPROVAL.value)

        with self.assertRaises(InvalidTransition):
            self.store.record_artifact(
                "artifact-task",
                "should-not-stick",
                {"value": True},
                transition_to=State.EXECUTING,
            )
        self.assertNotIn("should-not-stick", self.store.load("artifact-task")["artifacts"])

    def test_block_and_resume_default_to_current_state(self) -> None:
        self._create("blocked-task")
        self.store.transition("blocked-task", State.INTAKING)
        blocked = self.store.block("blocked-task", reason="requirements unavailable")
        self.assertEqual(blocked["state"], State.BLOCKED.value)
        self.assertEqual(blocked["resume"]["blocked_from"], State.INTAKING.value)
        self.assertEqual(blocked["resume"]["resume_from"], State.INTAKING.value)

        resumed = self.store.resume("blocked-task", note="requirements supplied")
        self.assertEqual(resumed["state"], State.INTAKING.value)
        self.assertIsNotNone(resumed["resume"]["resumed_at"])

    def test_block_accepts_active_resume_override(self) -> None:
        self._to_plan_approval("stale-base", "full")
        self.store.transition("stale-base", State.BASE_REVALIDATION)
        blocked = self.store.block(
            "stale-base",
            reason="base moved; revise the plan",
            resume_from=State.PLANNING,
        )
        self.assertEqual(blocked["resume"]["blocked_from"], State.BASE_REVALIDATION.value)
        self.assertEqual(blocked["resume"]["resume_from"], State.PLANNING.value)
        self.assertEqual(self.store.resume("stale-base")["state"], State.PLANNING.value)

        with self.assertRaises(InvalidTransition):
            self.store.block(
                "stale-base",
                reason="invalid continuation",
                resume_from=State.READY,
            )

    def test_block_rejects_future_resume_jump_without_mutation(self) -> None:
        original = self._create("future-from-new")
        with self.assertRaisesRegex(InvalidTransition, "재개할 수 있는 상태"):
            self.store.block(
                "future-from-new",
                reason="미래 단계 우회",
                resume_from=State.EXECUTING,
            )
        self.assertEqual(self.store.load("future-from-new"), original)

        waiting = self._to_spec_approval("future-from-spec", "full")
        with self.assertRaisesRegex(InvalidTransition, "재개할 수 있는 상태"):
            self.store.block(
                "future-from-spec",
                reason="승인 우회",
                resume_from=State.PLANNING,
            )
        self.assertEqual(self.store.load("future-from-spec"), waiting)

        waiting_plan = self._to_plan_approval("future-from-plan", "full")
        with self.assertRaisesRegex(InvalidTransition, "재개할 수 있는 상태"):
            self.store.block(
                "future-from-plan",
                reason="계획 승인 우회",
                resume_from=State.BASE_REVALIDATION,
            )
        self.assertEqual(self.store.load("future-from-plan"), waiting_plan)

    def test_block_preserves_required_controller_rollbacks(self) -> None:
        self._to_plan_approval("materialize-plan", "full")
        self.store.transition("materialize-plan", State.BASE_REVALIDATION)
        self.store.transition("materialize-plan", State.MATERIALIZING_WORKTREE)
        materializing = self.store.block(
            "materialize-plan",
            reason="계획을 다시 확인해야 합니다",
            resume_from=State.PLANNING,
        )
        self.assertEqual(materializing["resume"]["resume_from"], State.PLANNING.value)

        for task_id, resume_from in (
            ("execute-plan", State.PLANNING),
            ("execute-retry", State.EXECUTING),
        ):
            self._to_plan_approval(task_id, "full")
            for state in (
                State.BASE_REVALIDATION,
                State.MATERIALIZING_WORKTREE,
                State.EXECUTING,
            ):
                self.store.transition(task_id, state)
            blocked = self.store.block(
                task_id,
                reason="실행 복구",
                resume_from=resume_from,
            )
            self.assertEqual(blocked["resume"]["resume_from"], resume_from.value)

        for task_id, resume_from in (
            ("handoff-plan", State.PLANNING),
            ("handoff-execute", State.EXECUTING),
        ):
            self._to_plan_approval(task_id, "full")
            for state in (
                State.BASE_REVALIDATION,
                State.MATERIALIZING_WORKTREE,
                State.EXECUTING,
                State.HANDOFF,
            ):
                self.store.transition(task_id, state)
            blocked = self.store.block(
                task_id,
                reason="인계 복구",
                resume_from=resume_from,
            )
            self.assertEqual(blocked["resume"]["resume_from"], resume_from.value)

    def test_corrupt_resume_metadata_cannot_bypass_recovery_policy(self) -> None:
        self._to_spec_approval("tampered-resume", "full")
        self.store.block("tampered-resume", reason="입력 대기")
        path = self.store.state_path("tampered-resume")
        document = json.loads(path.read_text(encoding="utf-8"))
        document["resume"]["resume_from"] = State.EXECUTING.value
        path.write_text(json.dumps(document), encoding="utf-8")

        with self.assertRaisesRegex(CorruptState, "안전하지 않은 복구 상태"):
            self.store.load("tampered-resume")

    def test_resume_promotes_analyze_then_plan_targets(self) -> None:
        self._to_spec_approval("promote-analyze", "analyze")
        ready_analyze = self.store.transition("promote-analyze", State.READY)
        promoted_plan = self.store.resume("promote-analyze")
        self.assertEqual(promoted_plan["target"], "plan")
        self.assertEqual(promoted_plan["state"], State.PLANNING.value)
        self.assertEqual(promoted_plan["revision"], ready_analyze["revision"] + 1)

        promoted_plan = self.store.record_artifact(
            "promote-analyze",
            "plan",
            {"path": "plan.json", "sha256": "d" * 64},
            transition_to=State.AWAITING_PLAN_APPROVAL,
        )
        ready_plan = self.store.transition("promote-analyze", State.READY)
        promoted_full = self.store.resume("promote-analyze")
        self.assertEqual(promoted_full["target"], "full")
        self.assertEqual(promoted_full["state"], State.BASE_REVALIDATION.value)
        self.assertEqual(promoted_full["revision"], ready_plan["revision"] + 1)
        promotions = [
            event for event in promoted_full["history"] if event["event"] == "target_promoted"
        ]
        self.assertEqual([(item["from"], item["to"]) for item in promotions], [("analyze", "plan"), ("plan", "full")])

    def test_resume_ready_full_is_no_op(self) -> None:
        task_id = "ready-full"
        self._to_plan_approval(task_id, "full")
        for state in [
            State.BASE_REVALIDATION,
            State.MATERIALIZING_WORKTREE,
            State.EXECUTING,
            State.HANDOFF,
            State.READY,
        ]:
            ready = self.store.transition(task_id, state)
        resumed = self.store.resume(task_id)
        self.assertEqual(resumed, ready)
        with self.assertRaises(InvalidTransition):
            self.store.abandon(task_id, reason="already complete")

    def test_atomic_replace_failure_preserves_previous_state(self) -> None:
        original = self._create("atomic")
        with mock.patch.object(scv_state.os, "replace", side_effect=OSError("boom")):
            with self.assertRaisesRegex(OSError, "boom"):
                self.store.transition("atomic", State.INTAKING)

        self.assertEqual(self.store.load("atomic"), original)
        leftovers = list(self.store.task_dir("atomic").glob(".state.*.tmp"))
        self.assertEqual(leftovers, [])

    def test_invalidate_base_commits_all_effects_in_one_revision(self) -> None:
        task_id = "base-invalidated"
        self._to_plan_approval(task_id, "full")
        self.store.set_artifact(
            task_id,
            "plan_approval",
            {"approved": True, "sha256": "b" * 64, "approved_at": "earlier"},
        )
        before = self.store.transition(task_id, State.BASE_REVALIDATION)
        new_sha = "1" * 40

        after = self.store.invalidate_base(
            task_id,
            branch=self.branch,
            sha=new_sha,
            reason="기준 리비전 변경",
        )

        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(after["state"], State.BLOCKED.value)
        self.assertEqual(after["base"], {"branch": self.branch, "sha": new_sha})
        self.assertEqual(
            after["artifacts"]["base_change"]["previous_sha"],
            self.sha,
        )
        self.assertEqual(
            after["artifacts"]["base_change"]["current_sha"],
            new_sha,
        )
        approval = after["artifacts"]["plan_approval"]
        self.assertIs(approval["approved"], False)
        self.assertEqual(approval["sha256"], "b" * 64)
        self.assertIn("invalidated_at", approval)
        self.assertEqual(after["resume"]["blocked_from"], State.BASE_REVALIDATION.value)
        self.assertEqual(after["resume"]["resume_from"], State.PLANNING.value)
        self.assertEqual(self.store.resume(task_id)["state"], State.PLANNING.value)

    def test_invalidate_base_accepts_materializing_and_rejects_other_states(self) -> None:
        task_id = "base-materializing"
        self._to_plan_approval(task_id, "full")
        self.store.transition(task_id, State.BASE_REVALIDATION)
        self.store.transition(task_id, State.MATERIALIZING_WORKTREE)
        invalidated = self.store.invalidate_base(
            task_id,
            branch=self.branch,
            sha="2" * 40,
        )
        self.assertEqual(invalidated["state"], State.BLOCKED.value)
        self.assertEqual(
            invalidated["resume"]["blocked_from"],
            State.MATERIALIZING_WORKTREE.value,
        )

        invalid = self._create("base-wrong-state")
        with self.assertRaisesRegex(InvalidTransition, "상태에서만 가능"):
            self.store.invalidate_base(
                "base-wrong-state",
                branch=self.branch,
                sha="3" * 40,
            )
        self.assertEqual(self.store.load("base-wrong-state"), invalid)

    def test_invalidate_base_failure_preserves_entire_previous_record(self) -> None:
        task_id = "base-atomic-failure"
        self._to_plan_approval(task_id, "full")
        original = self.store.transition(task_id, State.BASE_REVALIDATION)
        with mock.patch.object(scv_state.os, "replace", side_effect=OSError("boom")):
            with self.assertRaisesRegex(OSError, "boom"):
                self.store.invalidate_base(
                    task_id,
                    branch=self.branch,
                    sha="4" * 40,
                )

        self.assertEqual(self.store.load(task_id), original)

    def test_update_base_worktree_artifact_and_abandon_are_persisted(self) -> None:
        self._create("metadata")
        new_sha = "1" * 40
        updated = self.store.update_base("metadata", branch="origin/main", sha=new_sha)
        self.assertEqual(updated["base"], {"branch": "origin/main", "sha": new_sha})

        self.store.transition("metadata", State.INTAKING)
        artifact = self.store.set_artifact("metadata", "request", {"text": "change it"})
        self.assertEqual(artifact["artifacts"]["request"], {"text": "change it"})
        abandoned = self.store.abandon("metadata", reason="superseded")
        self.assertEqual(abandoned["state"], State.ABANDONED.value)
        self.assertIsNotNone(abandoned["timestamps"]["abandoned_at"])

    def test_cli_emits_json_and_error_status(self) -> None:
        state_root = self.root / "cli-state"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = scv_state.main(
                [
                    "--repo",
                    str(self.repo),
                    "--state-root",
                    str(state_root),
                    "create",
                    "cli-task",
                    "--target",
                    "analyze",
                    "--base-branch",
                    self.branch,
                    "--base-sha",
                    self.sha,
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue())["task_id"], "cli-task")

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = scv_state.main(
                [
                    "--repo",
                    str(self.repo),
                    "--state-root",
                    str(state_root),
                    "transition",
                    "cli-task",
                    State.EXECUTING.value,
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("허용되지 않는 상태 전이", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
