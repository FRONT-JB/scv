from __future__ import annotations

import argparse
import fcntl
import io
import json
import os
from contextlib import redirect_stderr
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import execute  # noqa: E402
import improve  # noqa: E402
import learning  # noqa: E402
import scv  # noqa: E402
from scv_state import State, TaskStateStore  # noqa: E402


class SCVControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.run_git("init", "-b", "main")
        self.run_git("config", "user.name", "SCV Test")
        self.run_git("config", "user.email", "scv@example.test")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self.run_git("add", "README.md")
        self.run_git("commit", "-m", "initial")
        self.store = TaskStateStore(repo=self.repo, state_root=self.root / "state")

    def run_git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.strip()

    @staticmethod
    def args(**values: object) -> argparse.Namespace:
        return argparse.Namespace(**values)

    @staticmethod
    def write_ready_execution(
        run_dir: Path,
        *,
        task_id: str,
        plan_sha256: str,
        base_sha: str,
        workspace: Path,
    ) -> dict:
        attempt_dir = run_dir / "evidence" / "step-1" / "attempt-1"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        execute.atomic_write_json(
            attempt_dir / "worker-final.json",
            {
                "summary": "작업 완료",
                "changed_files": [],
                "tests_run": ["true"],
                "risks": [],
            },
        )
        execute.atomic_write_json(
            attempt_dir / "acceptance.json",
            [{"command": "true", "status": "passed", "returncode": 0}],
        )
        execute.atomic_write_json(
            attempt_dir / "verifier-final.json",
            {"verdict": "pass", "summary": "검증 통과", "findings": []},
        )
        final_dir = run_dir / "evidence" / "final" / "validation-1"
        final_dir.mkdir(parents=True, exist_ok=True)
        execute.atomic_write_json(
            final_dir / "acceptance.json",
            [{"command": "true", "status": "passed", "returncode": 0}],
        )
        execute.atomic_write_json(
            final_dir / "verifier-final.json",
            {"verdict": "pass", "summary": "최종 검증 통과", "findings": []},
        )
        index = {
            "schema_version": 1,
            "task_id": task_id,
            "plan_sha256": plan_sha256,
            "expected_base_sha": base_sha,
            "workspace": str(workspace.resolve()),
            "workspace_sha256": scv.workspace_fingerprint(workspace),
            "status": "ready",
            "completed_at": "2026-07-13T00:00:00Z",
            "steps": [
                {
                    "id": "step-1",
                    "status": "passed",
                    "blockers": [],
                    "attempts": [
                        {
                            "number": 1,
                            "status": "passed",
                            "started_at": "2026-07-13T00:00:00Z",
                            "finished_at": "2026-07-13T00:00:01Z",
                            "failure": None,
                            "evidence": str(attempt_dir.relative_to(run_dir)),
                            "evidence_sha256": execute.hash_evidence_directory(
                                attempt_dir
                            ),
                        }
                    ],
                }
            ],
            "final_acceptance": {"status": "passed", "commands": ["true"]},
            "final_verifier": {
                "verdict": "pass",
                "summary": "최종 검증 통과",
                "findings": [],
            },
            "final_validation": {
                "number": 1,
                "status": "passed",
                "evidence": str(final_dir.relative_to(run_dir)),
                "evidence_sha256": execute.hash_evidence_directory(final_dir),
            },
        }
        execute.atomic_write_json(run_dir / "index.json", index)
        return index

    def test_cli_help_uses_korean_terminal_labels(self) -> None:
        help_text = scv.build_parser().format_help()
        self.assertIn("사용법:", help_text)
        self.assertIn("위치 인자:", help_text)
        self.assertIn("선택 인자:", help_text)
        self.assertNotIn("usage:", help_text)

        error_text = io.StringIO()
        with redirect_stderr(error_text), self.assertRaises(SystemExit):
            scv.build_parser().parse_args(
                ["start", "wrong", "--task-id", "example", "--request", "요청"]
            )
        self.assertIn("인자 목표: 올바르지 않은 선택", error_text.getvalue())
        self.assertNotIn("argument 목표", error_text.getvalue())

        error_text = io.StringIO()
        with redirect_stderr(error_text), self.assertRaises(SystemExit):
            execute.build_parser().parse_args(
                ["--run-dir", "/tmp/example", "--timeout", "wrong"]
            )
        self.assertIn("올바른 int 값이 아닙니다", error_text.getvalue())
        self.assertNotIn("invalid int value", error_text.getvalue())

    def test_external_block_reason_is_single_line_and_bounded(self) -> None:
        reason = scv.safe_reason("첫 줄\n둘째 줄\x00" + ("가" * 5000))
        self.assertLessEqual(len(reason), 4000)
        self.assertFalse(any(ord(character) < 32 for character in reason))

    def start(self, task_id: str, target: str) -> dict:
        return scv.command_start(
            self.args(
                task_id=task_id,
                target=target,
                request="Change the sample safely",
                request_file=None,
                base="main",
            ),
            self.store,
            self.repo,
        )

    def submit_and_approve_spec(self, task_id: str) -> dict:
        spec = self.root / f"{task_id}-spec.md"
        spec.write_text("# Specification\n\nAcceptance is observable.\n", encoding="utf-8")
        scv.command_submit_spec(
            self.args(task_id=task_id, spec=str(spec)), self.store, self.repo
        )
        return scv.command_approve_spec(
            self.args(task_id=task_id), self.store, self.repo
        )

    def submit_and_approve_plan(
        self, task_id: str, *, schema_version: int = 1
    ) -> dict:
        plan = self.root / f"{task_id}-plan.json"
        document = {
            "schema_version": schema_version,
            "task_id": task_id,
            "task": "Change the tracked sample",
            "steps": [
                {
                    "id": "step-1",
                    "title": "Implement the change",
                    "instructions": "Update README.md and keep the behavior verified.",
                    "acceptance": ["git diff --check"],
                }
            ],
            "final_acceptance": ["git status --short"],
        }
        if schema_version == 2:
            document["loop_policy"] = {
                "max_attempts": 2,
                "detect_stagnation": True,
            }
        plan.write_text(
            json.dumps(document),
            encoding="utf-8",
        )
        scv.command_submit_plan(
            self.args(task_id=task_id, plan=str(plan)), self.store, self.repo
        )
        return scv.command_approve_plan(
            self.args(task_id=task_id), self.store, self.repo
        )

    def full_task_through_plan(self, task_id: str) -> dict:
        self.start(task_id, "full")
        self.submit_and_approve_spec(task_id)
        return self.submit_and_approve_plan(task_id)

    def test_analyze_target_finishes_without_a_worktree(self) -> None:
        self.start("analyze-task", "analyze")
        task = self.submit_and_approve_spec("analyze-task")

        self.assertEqual(State.READY.value, task["state"])
        self.assertIsNone(task["worktree"]["path"])
        self.assertEqual(1, len(scv.parse_worktrees(self.repo)))

    def test_plan_target_validates_json_and_finishes_without_a_worktree(self) -> None:
        self.start("plan-task", "plan")
        self.submit_and_approve_spec("plan-task")
        task = self.submit_and_approve_plan("plan-task")

        self.assertEqual(State.READY.value, task["state"])
        saved = json.loads((self.store.task_dir("plan-task") / "plan.json").read_text())
        self.assertEqual(self.run_git("rev-parse", "HEAD"), saved["expected_base_sha"])
        self.assertEqual(1, len(scv.parse_worktrees(self.repo)))

    def test_plan_target_accepts_the_v2_shallow_loop_policy(self) -> None:
        self.start("plan-v2", "plan")
        self.submit_and_approve_spec("plan-v2")

        task = self.submit_and_approve_plan("plan-v2", schema_version=2)

        self.assertEqual(State.READY.value, task["state"])
        saved = json.loads((self.store.task_dir("plan-v2") / "plan.json").read_text())
        self.assertEqual(2, saved["schema_version"])
        self.assertEqual(
            {"max_attempts": 2, "detect_stagnation": True},
            saved["loop_policy"],
        )

    def test_plan_to_full_promotion_preflights_before_state_change(self) -> None:
        self.start("promote-task", "plan")
        self.submit_and_approve_spec("promote-task")
        before = self.submit_and_approve_plan("promote-task")

        with mock.patch.object(
            scv,
            "preflight_start_runtime",
            side_effect=execute.InfrastructureBlocker("Seatbelt 사전 점검 실패"),
        ):
            with self.assertRaisesRegex(execute.InfrastructureBlocker, "Seatbelt"):
                scv.command_resume(
                    self.args(task_id="promote-task"), self.store, self.repo
                )

        after = self.store.load("promote-task")
        self.assertEqual("plan", after["target"])
        self.assertEqual(State.READY.value, after["state"])
        self.assertEqual(before["revision"], after["revision"])
        self.assertEqual(1, len(scv.parse_worktrees(self.repo)))

    def test_plan_submission_uses_the_executor_schema_before_state_changes(self) -> None:
        self.start("invalid-plan", "full")
        self.submit_and_approve_spec("invalid-plan")
        plan_path = self.root / "invalid-plan.json"
        base = {
            "schema_version": 1,
            "task_id": "invalid-plan",
            "task": "변경을 구현합니다",
            "steps": [
                {
                    "id": "step-1",
                    "title": "구현",
                    "instructions": "요구사항을 구현합니다",
                    "acceptance": ["git diff --check"],
                }
            ],
            "final_acceptance": [],
        }
        invalid_documents = []
        with_unknown_key = dict(base, unexpected=True)
        invalid_documents.append(with_unknown_key)
        with_boolean_timeout = json.loads(json.dumps(base))
        with_boolean_timeout["steps"][0]["timeout_seconds"] = True
        invalid_documents.append(with_boolean_timeout)
        with_large_timeout = json.loads(json.dumps(base))
        with_large_timeout["steps"][0]["timeout_seconds"] = 86_401
        invalid_documents.append(with_large_timeout)
        with_push = json.loads(json.dumps(base))
        with_push["steps"][0]["acceptance"] = ["git push origin HEAD"]
        invalid_documents.append(with_push)

        for document in invalid_documents:
            with self.subTest(document=document):
                plan_path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaisesRegex(scv.SCVError, "계획 검증 실패"):
                    scv.command_submit_plan(
                        self.args(task_id="invalid-plan", plan=str(plan_path)),
                        self.store,
                        self.repo,
                    )
                task = self.store.load("invalid-plan")
                self.assertEqual(State.PLANNING.value, task["state"])
                self.assertNotIn("plan", task["artifacts"])
                self.assertFalse((self.store.task_dir("invalid-plan") / "plan.json").exists())

    def test_materialize_is_rejected_before_plan_approval(self) -> None:
        self.start("early-task", "full")

        with self.assertRaises(scv.SCVError):
            scv.command_materialize(
                self.args(task_id="early-task", worktree=None, branch=None),
                self.store,
                self.repo,
            )

        self.assertEqual(1, len(scv.parse_worktrees(self.repo)))

    def test_base_drift_blocks_and_resumes_at_planning(self) -> None:
        approved = self.full_task_through_plan("drift-task")
        (self.repo / "README.md").write_text("base moved\n", encoding="utf-8")
        self.run_git("add", "README.md")
        self.run_git("commit", "-m", "move base")
        moved_sha = self.run_git("rev-parse", "HEAD")

        with self.assertRaisesRegex(scv.SCVError, "기준 리비전"):
            scv.command_materialize(
                self.args(task_id="drift-task", worktree=None, branch=None),
                self.store,
                self.repo,
            )

        blocked = self.store.load("drift-task")
        self.assertEqual(State.BLOCKED.value, blocked["state"])
        self.assertEqual(State.PLANNING.value, blocked["resume"]["resume_from"])
        self.assertEqual(approved["revision"] + 1, blocked["revision"])
        self.assertEqual(moved_sha, blocked["base"]["sha"])
        self.assertFalse(blocked["artifacts"]["plan_approval"]["approved"])
        resumed = scv.command_resume(
            self.args(task_id="drift-task"), self.store, self.repo
        )
        self.assertEqual(State.PLANNING.value, resumed["state"])
        self.assertEqual(1, len(scv.parse_worktrees(self.repo)))

    def test_approved_plan_tampering_blocks_before_worktree_creation(self) -> None:
        self.full_task_through_plan("tampered-plan")
        plan_path = self.store.task_dir("tampered-plan") / "plan.json"
        plan_path.write_text(plan_path.read_text() + "\n", encoding="utf-8")

        with self.assertRaisesRegex(scv.SCVError, "승인 후 plan 파일"):
            scv.command_materialize(
                self.args(task_id="tampered-plan", worktree=None, branch=None),
                self.store,
                self.repo,
            )

        blocked = self.store.load("tampered-plan")
        self.assertEqual(State.BLOCKED.value, blocked["state"])
        self.assertEqual(State.PLANNING.value, blocked["resume"]["resume_from"])
        self.assertEqual(1, len(scv.parse_worktrees(self.repo)))

    def test_unowned_existing_worktree_is_not_adopted(self) -> None:
        self.full_task_through_plan("foreign-worktree")
        path = scv.default_worktree(self.repo, "foreign-worktree")
        branch = "scv/foreign-worktree"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(path), "HEAD")

        with self.assertRaisesRegex(scv.SCVError, "생성 의도"):
            scv.command_materialize(
                self.args(task_id="foreign-worktree", worktree=None, branch=None),
                self.store,
                self.repo,
            )

        blocked = self.store.load("foreign-worktree")
        self.assertEqual(State.BLOCKED.value, blocked["state"])
        self.assertEqual(
            State.MATERIALIZING_WORKTREE.value,
            blocked["resume"]["resume_from"],
        )

    def test_explicit_clean_existing_worktree_can_be_adopted(self) -> None:
        task = self.full_task_through_plan("adopt-worktree")
        path = scv.default_worktree(self.repo, "adopt-worktree")
        branch = "scv/adopt-worktree"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(path), task["base"]["sha"])

        executing = scv.command_materialize(
            self.args(
                task_id="adopt-worktree",
                worktree=str(path),
                branch=branch,
                adopt_existing=True,
            ),
            self.store,
            self.repo,
        )

        self.assertEqual(State.EXECUTING.value, executing["state"])
        intent = executing["artifacts"]["worktree_intent"]
        self.assertTrue(intent["adopted_existing"])
        self.assertEqual(str(path), intent["path"])

    def test_owned_clean_worktree_can_be_adopted_after_controller_restart(self) -> None:
        task = self.full_task_through_plan("recover-worktree")
        self.store.transition("recover-worktree", State.MATERIALIZING_WORKTREE)
        path = scv.default_worktree(self.repo, "recover-worktree")
        branch = "scv/recover-worktree"
        self.store.set_artifact(
            "recover-worktree",
            "worktree_intent",
            {
                "path": str(path),
                "branch": branch,
                "base_sha": task["base"]["sha"],
                "recorded_at": scv.utc_now(),
            },
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run_git("worktree", "add", "-b", branch, str(path), task["base"]["sha"])

        executing = scv.command_materialize(
            self.args(task_id="recover-worktree", worktree=None, branch=None),
            self.store,
            self.repo,
        )

        self.assertEqual(State.EXECUTING.value, executing["state"])
        self.assertEqual(str(path), executing["worktree"]["path"])

    def test_exhausted_execution_requires_a_revised_plan(self) -> None:
        self.full_task_through_plan("exhausted-task")
        executing = scv.command_materialize(
            self.args(task_id="exhausted-task", worktree=None, branch=None),
            self.store,
            self.repo,
        )

        def fake_failure(command: list[str], *args: object, **kwargs: object):
            if len(command) > 1 and Path(command[1]).name == "execute.py":
                run_dir = Path(command[command.index("--run-dir") + 1])
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "index.json").write_text(
                    json.dumps(
                        {
                            "status": "failed",
                            "reason": "step-1 exhausted 3 attempts",
                            "steps": [
                                {
                                    "id": "step-1",
                                    "status": "failed",
                                    "attempts": [{}, {}, {}],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 1)
            return subprocess.run(command, *args, **kwargs)

        real_run = subprocess.run

        def failure_with_real_git(command: list[str], *args: object, **kwargs: object):
            if len(command) > 1 and Path(command[1]).name == "execute.py":
                return fake_failure(command, *args, **kwargs)
            return real_run(command, *args, **kwargs)

        failed_evidence = {
            "status": "failed",
            "reason": "step-1 exhausted 3 attempts",
            "steps": [
                {
                    "id": "step-1",
                    "status": "failed",
                    "attempts": [{}, {}, {}],
                }
            ],
        }
        with mock.patch.object(
            scv.subprocess, "run", side_effect=failure_with_real_git
        ), mock.patch.object(scv, "locked_status") as status:
            status.return_value.__enter__.return_value = failed_evidence
            with self.assertRaises(scv.SCVError):
                scv.command_execute(
                    self.args(task_id="exhausted-task", timeout=30), self.store, self.repo
                )

        blocked = self.store.load("exhausted-task")
        self.assertEqual(State.BLOCKED.value, blocked["state"])
        self.assertEqual(State.PLANNING.value, blocked["resume"]["resume_from"])
        self.assertTrue(blocked["artifacts"]["execution_failure"]["attempts_exhausted"])
        self.assertNotIn(
            "improvement_proposal_id", blocked["artifacts"]["execution_failure"]
        )
        proposals = learning.LearningStore(
            self.store.state_root.parent / "learning"
        ).list_proposals(task_id="exhausted-task")
        self.assertEqual([], proposals)
        resumed = scv.command_resume(
            self.args(task_id="exhausted-task"), self.store, self.repo
        )
        self.assertEqual(State.PLANNING.value, resumed["state"])
        self.assertTrue(Path(executing["worktree"]["path"]).is_dir())

    def test_stalled_execution_requires_a_revised_plan_before_resume(self) -> None:
        self.full_task_through_plan("stalled-task")
        scv.command_materialize(
            self.args(task_id="stalled-task", worktree=None, branch=None),
            self.store,
            self.repo,
        )
        failed_evidence = {
            "status": "failed",
            "reason": "같은 실패와 워크트리 상태가 반복되었습니다",
            "termination": {
                "code": "stalled",
                "message": "같은 실패와 워크트리 상태가 반복되었습니다",
                "next_action": "revise_plan",
                "step_id": "step-1",
                "attempt": 2,
            },
            "steps": [
                {
                    "id": "step-1",
                    "status": "failed",
                    "attempts": [{}, {}],
                }
            ],
        }
        real_run = subprocess.run

        def failure_with_real_git(command: list[str], *args: object, **kwargs: object):
            if len(command) > 1 and Path(command[1]).name == "execute.py":
                run_dir = Path(command[command.index("--run-dir") + 1])
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "index.json").write_text("{}\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 1)
            return real_run(command, *args, **kwargs)

        with mock.patch.object(
            scv.subprocess, "run", side_effect=failure_with_real_git
        ), mock.patch.object(scv, "locked_status") as status:
            status.return_value.__enter__.return_value = failed_evidence
            with self.assertRaises(scv.SCVError):
                scv.command_execute(
                    self.args(task_id="stalled-task", timeout=30),
                    self.store,
                    self.repo,
                )

        blocked = self.store.load("stalled-task")
        failure = blocked["artifacts"]["execution_failure"]
        self.assertEqual(State.PLANNING.value, blocked["resume"]["resume_from"])
        self.assertFalse(failure["attempts_exhausted"])
        self.assertTrue(failure["plan_revision_required"])
        self.assertEqual("stalled", failure["termination_code"])
        resumed = scv.command_resume(
            self.args(task_id="stalled-task"), self.store, self.repo
        )
        self.assertEqual(State.PLANNING.value, resumed["state"])
        with self.assertRaisesRegex(scv.SCVError, "계획을 수정"):
            scv.command_submit_plan(
                self.args(
                    task_id="stalled-task",
                    plan=str(self.root / "stalled-task-plan.json"),
                ),
                self.store,
                self.repo,
            )

    def test_concurrent_controller_execute_is_rejected_without_state_change(self) -> None:
        self.full_task_through_plan("concurrent-execute")
        scv.command_materialize(
            self.args(task_id="concurrent-execute", worktree=None, branch=None),
            self.store,
            self.repo,
        )
        lock_path = self.store.task_dir("concurrent-execute") / ".controller-execute.lock"
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(scv.SCVError, "다른 SCV 실행기"):
                scv.command_execute(
                    self.args(task_id="concurrent-execute", timeout=30),
                    self.store,
                    self.repo,
                )
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

        task = self.store.load("concurrent-execute")
        self.assertEqual(State.EXECUTING.value, task["state"])
        self.assertNotIn("execution_failure", task["artifacts"])

    def test_busy_executor_exit_does_not_block_or_mutate_task(self) -> None:
        self.full_task_through_plan("busy-executor")
        scv.command_materialize(
            self.args(task_id="busy-executor", worktree=None, branch=None),
            self.store,
            self.repo,
        )
        busy = subprocess.CompletedProcess(
            ["execute.py"], scv.EXECUTION_BUSY_EXIT_CODE
        )
        with mock.patch.object(scv.subprocess, "run", return_value=busy):
            with self.assertRaisesRegex(scv.SCVError, "실행 증거를 사용 중"):
                scv.command_execute(
                    self.args(task_id="busy-executor", timeout=30),
                    self.store,
                    self.repo,
                )

        task = self.store.load("busy-executor")
        self.assertEqual(State.EXECUTING.value, task["state"])
        self.assertNotIn("execution_failure", task["artifacts"])

    def test_execute_blocks_if_workspace_changes_after_executor_validation(self) -> None:
        self.full_task_through_plan("execute-drift")
        executing = scv.command_materialize(
            self.args(task_id="execute-drift", worktree=None, branch=None),
            self.store,
            self.repo,
        )
        worktree = Path(executing["worktree"]["path"])
        real_run = subprocess.run

        def fake_executor(command: list[str], *args: object, **kwargs: object):
            if len(command) > 1 and Path(command[1]).name == "execute.py":
                run_dir = Path(command[command.index("--run-dir") + 1])
                self.write_ready_execution(
                    run_dir,
                    task_id="execute-drift",
                    plan_sha256=run_dir.name,
                    base_sha=command[command.index("--expected-base") + 1],
                    workspace=worktree,
                )
                (worktree / "README.md").write_text(
                    "실행기 검증 이후 변경\n", encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)

        with mock.patch.object(scv.subprocess, "run", side_effect=fake_executor):
            with self.assertRaisesRegex(scv.SCVError, "워크트리가 변경"):
                scv.command_execute(
                    self.args(task_id="execute-drift", timeout=30),
                    self.store,
                    self.repo,
                )

        blocked = self.store.load("execute-drift")
        self.assertEqual(State.BLOCKED.value, blocked["state"])
        self.assertEqual(State.EXECUTING.value, blocked["resume"]["resume_from"])
        self.assertNotIn("execution", blocked["artifacts"])

    def test_full_flow_retains_worktree_after_handoff(self) -> None:
        self.full_task_through_plan("full-task")
        executing = scv.command_materialize(
            self.args(task_id="full-task", worktree=None, branch=None),
            self.store,
            self.repo,
        )
        worktree = Path(executing["worktree"]["path"])
        self.assertTrue(worktree.is_dir())
        self.assertEqual(State.EXECUTING.value, executing["state"])

        real_run = subprocess.run

        def fake_executor(command: list[str], *args: object, **kwargs: object):
            if len(command) > 1 and Path(command[1]).name == "execute.py":
                run_dir = Path(command[command.index("--run-dir") + 1])
                fake_root = Path(command[command.index("--root") + 1])
                (fake_root / "README.md").write_text("implemented\n", encoding="utf-8")
                self.write_ready_execution(
                    run_dir,
                    task_id="full-task",
                    plan_sha256=run_dir.name,
                    base_sha=command[command.index("--expected-base") + 1],
                    workspace=fake_root,
                )
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)

        with mock.patch.object(scv.subprocess, "run", side_effect=fake_executor):
            handoff = scv.command_execute(
                self.args(task_id="full-task", timeout=30), self.store, self.repo
            )
        self.assertEqual(State.HANDOFF.value, handoff["state"])

        ready = scv.command_handoff(
            self.args(task_id="full-task"), self.store, self.repo
        )
        self.assertEqual(State.READY.value, ready["state"])
        self.assertTrue(worktree.is_dir(), "READY must not remove the worktree")
        handoff_text = (self.store.task_dir("full-task") / "handoff.md").read_text()
        self.assertIn("README.md", handoff_text)
        self.assertIn("의도적으로 보존", handoff_text)

    def test_handoff_blocks_if_worktree_changed_after_execution(self) -> None:
        self.full_task_through_plan("handoff-drift")
        executing = scv.command_materialize(
            self.args(task_id="handoff-drift", worktree=None, branch=None),
            self.store,
            self.repo,
        )
        worktree = Path(executing["worktree"]["path"])
        real_run = subprocess.run

        def fake_executor(command: list[str], *args: object, **kwargs: object):
            if len(command) > 1 and Path(command[1]).name == "execute.py":
                run_dir = Path(command[command.index("--run-dir") + 1])
                fake_root = Path(command[command.index("--root") + 1])
                self.write_ready_execution(
                    run_dir,
                    task_id="handoff-drift",
                    plan_sha256=run_dir.name,
                    base_sha=command[command.index("--expected-base") + 1],
                    workspace=fake_root,
                )
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)

        with mock.patch.object(scv.subprocess, "run", side_effect=fake_executor):
            scv.command_execute(
                self.args(task_id="handoff-drift", timeout=30), self.store, self.repo
            )
        (worktree / "README.md").write_text("late drift\n", encoding="utf-8")

        with self.assertRaisesRegex(scv.SCVError, "워크트리가 변경"):
            scv.command_handoff(
                self.args(task_id="handoff-drift"), self.store, self.repo
            )
        blocked = self.store.load("handoff-drift")
        self.assertEqual(State.EXECUTING.value, blocked["resume"]["resume_from"])

    def test_handoff_blocks_clean_committed_drift_after_execution(self) -> None:
        self.full_task_through_plan("handoff-committed-drift")
        executing = scv.command_materialize(
            self.args(
                task_id="handoff-committed-drift", worktree=None, branch=None
            ),
            self.store,
            self.repo,
        )
        worktree = Path(executing["worktree"]["path"])
        real_run = subprocess.run

        def fake_executor(command: list[str], *args: object, **kwargs: object):
            if len(command) > 1 and Path(command[1]).name == "execute.py":
                run_dir = Path(command[command.index("--run-dir") + 1])
                self.write_ready_execution(
                    run_dir,
                    task_id="handoff-committed-drift",
                    plan_sha256=run_dir.name,
                    base_sha=command[command.index("--expected-base") + 1],
                    workspace=worktree,
                )
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)

        with mock.patch.object(scv.subprocess, "run", side_effect=fake_executor):
            scv.command_execute(
                self.args(task_id="handoff-committed-drift", timeout=30),
                self.store,
                self.repo,
            )

        (worktree / "README.md").write_text("검증되지 않은 커밋\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(worktree), "add", "README.md"], check=True
        )
        subprocess.run(
            ["git", "-C", str(worktree), "commit", "-m", "unverified drift"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual("", scv.git(worktree, "status", "--short"))
        execution_fingerprint = self.store.load("handoff-committed-drift")[
            "artifacts"
        ]["execution"]["workspace_sha256"]
        self.assertNotEqual(
            execution_fingerprint, scv.workspace_fingerprint(worktree)
        )

        with self.assertRaisesRegex(scv.SCVError, "워크트리 HEAD가 변경"):
            scv.command_handoff(
                self.args(task_id="handoff-committed-drift"),
                self.store,
                self.repo,
            )

        blocked = self.store.load("handoff-committed-drift")
        self.assertEqual(State.BLOCKED.value, blocked["state"])
        self.assertEqual(State.EXECUTING.value, blocked["resume"]["resume_from"])
        self.assertFalse(
            (self.store.task_dir("handoff-committed-drift") / "handoff.md").exists()
        )

    def test_handoff_revalidates_execution_evidence_before_ready(self) -> None:
        self.full_task_through_plan("handoff-evidence-drift")
        scv.command_materialize(
            self.args(
                task_id="handoff-evidence-drift", worktree=None, branch=None
            ),
            self.store,
            self.repo,
        )
        real_run = subprocess.run

        def fake_executor(command: list[str], *args: object, **kwargs: object):
            if len(command) > 1 and Path(command[1]).name == "execute.py":
                run_dir = Path(command[command.index("--run-dir") + 1])
                fake_root = Path(command[command.index("--root") + 1])
                self.write_ready_execution(
                    run_dir,
                    task_id="handoff-evidence-drift",
                    plan_sha256=run_dir.name,
                    base_sha=command[command.index("--expected-base") + 1],
                    workspace=fake_root,
                )
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)

        with mock.patch.object(scv.subprocess, "run", side_effect=fake_executor):
            scv.command_execute(
                self.args(task_id="handoff-evidence-drift", timeout=30),
                self.store,
                self.repo,
            )

        task = self.store.load("handoff-evidence-drift")
        run_dir = self.store.task_dir("handoff-evidence-drift") / Path(
            task["artifacts"]["execution"]["path"]
        ).parent
        execute.atomic_write_json(
            run_dir / "index.json",
            {"schema_version": 1, "status": "failed", "steps": []},
        )

        with self.assertRaisesRegex(scv.SCVError, "실행 인덱스가 변경"):
            scv.command_handoff(
                self.args(task_id="handoff-evidence-drift"),
                self.store,
                self.repo,
            )

        blocked = self.store.load("handoff-evidence-drift")
        self.assertEqual(State.BLOCKED.value, blocked["state"])
        self.assertEqual(State.EXECUTING.value, blocked["resume"]["resume_from"])
        self.assertFalse((self.store.task_dir("handoff-evidence-drift") / "handoff.md").exists())

    def test_status_revalidates_ready_full_execution_evidence(self) -> None:
        self.full_task_through_plan("ready-status-evidence")
        scv.command_materialize(
            self.args(task_id="ready-status-evidence", worktree=None, branch=None),
            self.store,
            self.repo,
        )
        real_run = subprocess.run

        def fake_executor(command: list[str], *args: object, **kwargs: object):
            if len(command) > 1 and Path(command[1]).name == "execute.py":
                run_dir = Path(command[command.index("--run-dir") + 1])
                fake_root = Path(command[command.index("--root") + 1])
                self.write_ready_execution(
                    run_dir,
                    task_id="ready-status-evidence",
                    plan_sha256=run_dir.name,
                    base_sha=command[command.index("--expected-base") + 1],
                    workspace=fake_root,
                )
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)

        with mock.patch.object(scv.subprocess, "run", side_effect=fake_executor):
            scv.command_execute(
                self.args(task_id="ready-status-evidence", timeout=30),
                self.store,
                self.repo,
            )
        scv.command_handoff(
            self.args(task_id="ready-status-evidence"), self.store, self.repo
        )

        status = scv.command_status(
            self.args(task_id="ready-status-evidence"), self.store, self.repo
        )
        self.assertEqual("verified", status["execution_integrity"]["status"])
        worktree = Path(status["worktree"]["path"])
        late_change = worktree / "검증-이후.txt"
        late_change.write_text("late drift\n", encoding="utf-8")
        with self.assertRaisesRegex(scv.SCVError, "워크트리 내용이 변경"):
            scv.command_status(
                self.args(task_id="ready-status-evidence"), self.store, self.repo
            )
        self.assertEqual(
            State.READY.value,
            self.store.load("ready-status-evidence")["state"],
        )
        late_change.unlink()
        run_dir = Path(status["execution_integrity"]["index"]).parent
        final_verifier = (
            run_dir
            / "evidence"
            / "final"
            / "validation-1"
            / "verifier-final.json"
        )
        final_verifier.write_text('{"verdict":"fail"}\n', encoding="utf-8")

        with self.assertRaisesRegex(scv.SCVError, "무결성 검증에 실패"):
            scv.command_status(
                self.args(task_id="ready-status-evidence"), self.store, self.repo
            )
        self.assertEqual(
            State.READY.value, self.store.load("ready-status-evidence")["state"]
        )

    def test_status_reports_sanitized_progress_while_executor_owns_run_lock(self) -> None:
        self.full_task_through_plan("active-progress")
        task = scv.command_materialize(
            self.args(task_id="active-progress", worktree=None, branch=None),
            self.store,
            self.repo,
        )
        plan_sha256 = task["artifacts"]["plan"]["sha256"]
        run_dir = self.store.task_dir("active-progress") / "runs" / plan_sha256
        execute.atomic_write_json(
            run_dir / "index.json",
            {
                "schema_version": 1,
                "task_id": "active-progress",
                "plan_sha256": plan_sha256,
                "expected_base_sha": task["base"]["sha"],
                "workspace": str(Path(task["worktree"]["path"]).resolve()),
                "status": "running",
                "updated_at": "2026-07-13T00:00:00Z",
                "progress": {
                    "stage": "worker",
                    "step_id": "step-1",
                    "step_position": 1,
                    "total_steps": 1,
                    "attempt": 2,
                },
                "steps": [
                    {
                        "id": "step-1",
                        "status": "running",
                        "attempts": [],
                        "blockers": [],
                    }
                ],
            },
        )
        revision = task["revision"]

        with execute.run_directory_lock(run_dir):
            status = scv.command_status(
                self.args(task_id="active-progress"), self.store, self.repo
            )

        self.assertEqual(State.EXECUTING.value, status["state"])
        self.assertEqual(revision, status["revision"])
        self.assertEqual(
            {
                "status": "running",
                "stage": "worker",
                "stage_label": "단계 구현",
                "completed_steps": 0,
                "total_steps": 1,
                "current_step": {
                    "id": "step-1",
                    "position": 1,
                    "total": 1,
                    "status": "running",
                },
                "attempt": 2,
                "message": "step-1의 2차 worker가 구현을 진행하고 있습니다.",
                "updated_at": "2026-07-13T00:00:00Z",
                "scv_line": "Orders received.",
            },
            status["execution_progress"],
        )

    def test_status_reports_starting_progress_before_executor_creates_index(self) -> None:
        self.full_task_through_plan("starting-progress")
        task = scv.command_materialize(
            self.args(task_id="starting-progress", worktree=None, branch=None),
            self.store,
            self.repo,
        )

        status = scv.command_status(
            self.args(task_id="starting-progress"), self.store, self.repo
        )

        self.assertEqual(State.EXECUTING.value, status["state"])
        self.assertEqual(
            {
                "status": "pending",
                "stage": "starting",
                "stage_label": "실행 환경 준비",
                "completed_steps": 0,
                "total_steps": 1,
                "message": "실행 환경을 준비하고 있습니다.",
                "scv_line": "Reportin' for duty.",
            },
            status["execution_progress"],
        )
        self.assertEqual(task["revision"], status["revision"])

    def test_real_subprocess_contract_reaches_ready_with_fake_codex(self) -> None:
        self.full_task_through_plan("subprocess-task")
        scv.command_materialize(
            self.args(task_id="subprocess-task", worktree=None, branch=None),
            self.store,
            self.repo,
        )
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        fake_codex = fake_bin / "codex"
        fake_codex.write_text(
            """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args == ['--version']:
    print('codex-cli 0.144.1')
    raise SystemExit(0)
if args == ['exec', '--help']:
    print('--config --cd --color --ephemeral --ignore-user-config --json --output-schema --output-last-message --sandbox')
    raise SystemExit(0)
if args == ['sandbox', '--help']:
    print('-P --sandbox-state-disable-network -C')
    raise SystemExit(0)
if args and args[0] == 'sandbox':
    separator = args.index('--')
    command = args[separator + 1:]
    os.execvp(command[0], command)
output = Path(args[args.index('--output-last-message') + 1])
schema = Path(args[args.index('--output-schema') + 1]).name
if 'verifier' in schema:
    value = {'verdict': 'pass', 'summary': '검증 통과', 'findings': []}
else:
    Path('README.md').write_text('implemented\\n', encoding='utf-8')
    value = {
        'summary': '작업 완료',
        'changed_files': ['README.md'],
        'tests_run': ['git diff --check'],
        'risks': [],
    }
output.write_text(json.dumps(value), encoding='utf-8')
print(json.dumps({'type': 'result'}))
""",
            encoding="utf-8",
        )
        fake_codex.chmod(0o755)
        environment = dict(os.environ)
        environment["PATH"] = str(fake_bin) + os.pathsep + environment.get("PATH", "")
        command = [
            os.fspath(Path(scv.sys.executable)),
            os.fspath(Path(scv.__file__)),
            "--repo",
            os.fspath(self.repo),
            "--state-root",
            os.fspath(self.root / "state"),
            "execute",
            "subprocess-task",
            "--timeout",
            "30",
        ]
        result = subprocess.run(
            command,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        handoff = self.store.load("subprocess-task")
        self.assertEqual(State.HANDOFF.value, handoff["state"])
        execution = handoff["artifacts"]["execution"]
        self.assertTrue((self.store.task_dir("subprocess-task") / execution["path"]).is_file())
        worktree = Path(handoff["worktree"]["path"])
        self.assertEqual("implemented\n", (worktree / "README.md").read_text())
        self.assertEqual("base\n", (self.repo / "README.md").read_text())

        ready = scv.command_handoff(
            self.args(task_id="subprocess-task"), self.store, self.repo
        )
        self.assertEqual(State.READY.value, ready["state"])
        self.assertTrue(worktree.is_dir())
        self.assertIn(
            "README.md",
            (self.store.task_dir("subprocess-task") / "handoff.md").read_text(),
        )
        status = scv.command_status(
            self.args(task_id="subprocess-task"), self.store, self.repo
        )
        self.assertEqual("verified", status["execution_integrity"]["status"])

    def test_improve_approves_candidate_only_with_ready_execution_evidence(self) -> None:
        task_id = "learning-approval"
        self.full_task_through_plan(task_id)
        task = scv.command_materialize(
            self.args(task_id=task_id, worktree=None, branch=None),
            self.store,
            self.repo,
        )
        plan = task["artifacts"]["plan"]
        worktree = Path(task["worktree"]["path"])
        run_relative = Path("runs") / plan["sha256"]
        run_dir = self.store.task_dir(task_id) / run_relative
        index = self.write_ready_execution(
            run_dir,
            task_id=task_id,
            plan_sha256=plan["sha256"],
            base_sha=task["base"]["sha"],
            workspace=worktree,
        )
        index_sha256 = scv.hashlib.sha256(
            (run_dir / "index.json").read_bytes()
        ).hexdigest()
        self.store.set_artifact(
            task_id,
            "execution",
            {
                "path": str(run_relative / "index.json"),
                "plan_sha256": plan["sha256"],
                "index_sha256": index_sha256,
                "workspace_sha256": index["workspace_sha256"],
                "completed_at": scv.utc_now(),
            },
        )
        self.store.transition(task_id, State.HANDOFF)

        learning_store = learning.LearningStore(
            self.store.state_root.parent / "learning"
        )
        failure = learning.build_failure_record(
            task_id=task_id,
            plan_sha256=plan["sha256"],
            step_id="step-1",
            attempt_number=1,
            stage="acceptance",
            message="검증 실패",
            evidence_sha256="a" * 64,
        )
        observation = learning_store.record_observation(
            failure,
            {
                "classification": "implementation",
                "diagnosis": "경계 조건이 누락되었습니다.",
                "failed_approaches": [],
                "next_actions": ["경계 조건을 구현합니다."],
                "verification_checks": ["같은 검증을 실행합니다."],
                "candidate_lesson": "경계 조건을 먼저 재현합니다.",
            },
            analyst_evidence_sha256="b" * 64,
        )
        candidate = learning_store.create_candidate(
            observation["observation_id"],
            successful_evidence_sha256=index["steps"][0]["attempts"][0][
                "evidence_sha256"
            ],
        )
        with self.assertRaises(scv.SCVError):
            improve.command_approve(
                self.args(task_id=task_id, lesson_id=candidate["lesson_id"]),
                self.store,
                learning_store,
            )

        self.store.transition(task_id, State.READY)
        approval_evidence = json.loads(json.dumps(index))
        passed_attempt = approval_evidence["steps"][0]["attempts"][0]
        passed_attempt["number"] = 3
        passed_attempt["learning"] = {
            "status": "candidate-created",
            "source_observation_id": observation["observation_id"],
            "candidate_lesson_id": candidate["lesson_id"],
        }
        approval_evidence["steps"][0]["attempts"].insert(0, {
            "number": 1,
            "status": "failed",
            "started_at": "2026-07-13T00:00:00Z",
            "finished_at": "2026-07-13T00:00:01Z",
            "failure": {"stage": "acceptance", "message": "검증 실패"},
            "evidence": "evidence/step-1/attempt-1-failed",
            "evidence_sha256": failure["evidence_sha256"],
            "learning": {
                "status": "analyzed",
                "signature": failure["signature"],
                "observation_id": observation["observation_id"],
                "analysis_evidence": "analysis/step-1/attempt-1",
                "analysis_evidence_sha256": observation[
                    "analyst_evidence_sha256"
                ],
            },
        })
        approval_evidence["steps"][0]["attempts"].insert(1, {
            "number": 2,
            "status": "failed",
            "started_at": "2026-07-13T00:00:02Z",
            "finished_at": "2026-07-13T00:00:03Z",
            "failure": {"stage": "acceptance", "message": "검증 실패"},
            "evidence": "evidence/step-1/attempt-2-failed",
            "evidence_sha256": failure["evidence_sha256"],
            "learning": {
                "status": "reused",
                "signature": failure["signature"],
                "observation_id": observation["observation_id"],
                "active_lesson_ids": [],
            },
        })
        source_attempt = approval_evidence["steps"][0]["attempts"][0]
        for non_learnable_status in ("timed_out", "interrupted", "cancelled"):
            with self.subTest(source_status=non_learnable_status):
                source_attempt["status"] = non_learnable_status
                with mock.patch.object(improve, "locked_status") as status:
                    status.return_value.__enter__.return_value = approval_evidence
                    with self.assertRaisesRegex(scv.SCVError, "연결되지"):
                        improve.command_approve(
                            self.args(
                                task_id=task_id,
                                lesson_id=candidate["lesson_id"],
                            ),
                            self.store,
                            learning_store,
                        )
        source_attempt["status"] = "failed"
        observation_path = (
            learning_store.observations / f"{observation['observation_id']}.json"
        )
        hidden_observation = observation_path.with_suffix(".missing")
        observation_path.rename(hidden_observation)
        with self.assertRaises(learning.LearningError):
            improve.command_approve(
                self.args(task_id=task_id, lesson_id=candidate["lesson_id"]),
                self.store,
                learning_store,
            )
        hidden_observation.rename(observation_path)

        with mock.patch.object(improve, "locked_status") as status:
            status.return_value.__enter__.return_value = approval_evidence
            active = improve.command_approve(
                self.args(task_id=task_id, lesson_id=candidate["lesson_id"]),
                self.store,
                learning_store,
            )

        self.assertEqual("active", active["status"])
        self.assertEqual(
            index_sha256,
            active["validation"]["approval_evidence"]["execution_index_sha256"],
        )

    def test_improve_queue_marks_controller_proposal_evidence_status(self) -> None:
        task_id = "controller-proposal"
        self.full_task_through_plan(task_id)
        task = scv.command_materialize(
            self.args(task_id=task_id, worktree=None, branch=None),
            self.store,
            self.repo,
        )
        plan = task["artifacts"]["plan"]
        learning_store = learning.LearningStore(
            self.store.state_root.parent / "learning"
        )
        failure = learning.build_failure_record(
            task_id=task_id,
            plan_sha256=plan["sha256"],
            step_id="step-1",
            attempt_number=1,
            stage="acceptance",
            message="제어기 검증 실패",
            evidence_sha256="a" * 64,
            scope="step-1",
        )
        observation = learning_store.record_observation(
            failure,
            {
                "classification": "controller",
                "diagnosis": "제어기 경계가 잘못되었습니다.",
                "failed_approaches": [],
                "next_actions": ["회귀 테스트를 추가합니다."],
                "verification_checks": ["전체 SCV 테스트를 실행합니다."],
                "candidate_lesson": "제어기 회귀를 별도 소스 태스크로 수정합니다.",
            },
            analyst_evidence_sha256="b" * 64,
        )
        proposal = learning_store.create_proposal(
            observation["observation_id"], kind="controller-defect"
        )
        evidence = {
            "task_id": task_id,
            "plan_sha256": plan["sha256"],
            "steps": [
                {
                    "id": "step-1",
                    "attempts": [
                        {
                            "number": 1,
                            "status": "failed",
                            "failure": {"stage": "acceptance"},
                            "evidence_sha256": failure["evidence_sha256"],
                            "learning": {
                                "status": "analyzed",
                                "signature": failure["signature"],
                                "observation_id": observation["observation_id"],
                                "analysis_evidence_sha256": observation[
                                    "analyst_evidence_sha256"
                                ],
                                "proposal_id": proposal["proposal_id"],
                            },
                        }
                    ],
                }
            ],
        }
        args = self.args(task_id=task_id)
        with mock.patch.object(improve, "locked_status") as status:
            status.return_value.__enter__.return_value = evidence
            listed = improve.command_list(args, self.store, learning_store)
        self.assertEqual("verified", listed["proposals"][0]["evidence_status"])

        source_attempt = evidence["steps"][0]["attempts"][0]
        for non_learnable_status in ("timed_out", "interrupted", "cancelled"):
            with self.subTest(proposal_source_status=non_learnable_status):
                source_attempt["status"] = non_learnable_status
                with mock.patch.object(improve, "locked_status") as status:
                    status.return_value.__enter__.return_value = evidence
                    listed = improve.command_list(args, self.store, learning_store)
                self.assertEqual(
                    "invalid", listed["proposals"][0]["evidence_status"]
                )
        source_attempt["status"] = "failed"

        handoff_args = self.args(
            proposal_id=proposal["proposal_id"],
            repair_repo=str(self.repo),
            repair_task_id="controller-repair",
        )
        with mock.patch.object(improve, "locked_status") as status:
            status.return_value.__enter__.return_value = evidence
            with self.assertRaisesRegex(scv.SCVError, "SCV 소스 파일"):
                improve.command_proposal_handoff(
                    handoff_args, self.store, learning_store
                )

        source_root = self.repo / "plugins" / "codex" / "scv"
        source_files = {
            ".codex-plugin/plugin.json": json.dumps(
                {"name": "scv", "version": "0.2.0"}
            ),
            "scripts/execute.py": "# SCV executor source\n",
            "scripts/improve.py": "# SCV improvement source\n",
            "skills/workflow/SKILL.md": "# SCV Workflow\n",
            "skills/improve/SKILL.md": "# SCV Improve\n",
        }
        for relative, contents in source_files.items():
            path = source_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")

        with mock.patch.object(improve, "locked_status") as status:
            status.return_value.__enter__.return_value = evidence
            with self.assertRaisesRegex(scv.SCVError, "Git 추적 대상"):
                improve.command_proposal_handoff(
                    handoff_args, self.store, learning_store
                )

        self.run_git("add", "plugins/codex/scv")
        self.run_git("commit", "-m", "add tracked scv source")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.repo)}):
            with self.assertRaisesRegex(scv.SCVError, "설치된 Codex 플러그인"):
                improve._require_repair_source_checkout(self.repo)
        repair_store = TaskStateStore(repo=self.repo)
        repair_store.create(
            "controller-repair",
            target="full",
            base_branch="main",
            base_sha=self.run_git("rev-parse", "HEAD"),
            artifacts={
                "request": {
                    "text": f"SCV 개선 제안 {proposal['proposal_id']}를 별도 태스크로 수리합니다."
                }
            },
            initial_state=State.INTAKING,
        )

        same_task_args = self.args(
            proposal_id=proposal["proposal_id"],
            repair_repo=str(self.repo),
            repair_task_id=task_id,
        )
        with mock.patch.object(improve, "locked_status") as status:
            status.return_value.__enter__.return_value = evidence
            with self.assertRaisesRegex(scv.SCVError, "다른 수리 태스크"):
                improve.command_proposal_handoff(
                    same_task_args, self.store, learning_store
                )

        with mock.patch.object(improve, "locked_status") as status:
            status.return_value.__enter__.return_value = evidence
            handed_off = improve.command_proposal_handoff(
                handoff_args, self.store, learning_store
            )
        self.assertEqual("handed-off", handed_off["status"])
        self.assertEqual(
            str(source_root.resolve()),
            handed_off["handoff"]["repair_plugin_root"],
        )

        evidence["steps"][0]["attempts"][0]["learning"][
            "analysis_evidence_sha256"
        ] = "c" * 64
        with mock.patch.object(improve, "locked_status") as status:
            status.return_value.__enter__.return_value = evidence
            listed = improve.command_list(args, self.store, learning_store)
        self.assertEqual("invalid", listed["proposals"][0]["evidence_status"])

    def test_revised_spec_replaces_fingerprint_without_leaving_wait_state(self) -> None:
        self.start("revision-task", "analyze")
        first = self.root / "spec-one.md"
        second = self.root / "spec-two.md"
        first.write_text("one", encoding="utf-8")
        second.write_text("two", encoding="utf-8")
        scv.command_submit_spec(
            self.args(task_id="revision-task", spec=str(first)), self.store, self.repo
        )
        revised = scv.command_submit_spec(
            self.args(task_id="revision-task", spec=str(second)), self.store, self.repo
        )

        self.assertEqual(State.AWAITING_SPEC_APPROVAL.value, revised["state"])
        self.assertFalse(revised["artifacts"]["spec_approval"]["approved"])
        expected = scv.fingerprint(
            self.store.task_dir("revision-task") / "spec.md",
            self.store.task_dir("revision-task"),
        )["sha256"]
        self.assertEqual(expected, revised["artifacts"]["spec"]["sha256"])


if __name__ == "__main__":
    unittest.main()
