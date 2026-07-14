from __future__ import annotations

import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import execute  # noqa: E402


class FakeRunner:
    def __init__(self, root: Path, sha: str) -> None:
        self.root = root.resolve()
        self.sha = sha
        self.calls: list[dict[str, object]] = []
        self.worker_behaviors: list[object] = []
        self.verifier_behaviors: list[object] = []
        self.failure_analyst_behaviors: list[object] = []
        self.acceptance_behaviors: list[object] = []
        self.default_worker_behavior: object = {
            "summary": "implemented",
            "changed_files": ["src/example.py"],
            "tests_run": [],
            "risks": [],
        }
        self.default_verifier_behavior: object = {
            "verdict": "pass",
            "summary": "requirements satisfied",
            "findings": [],
        }
        self.default_failure_analyst_behavior: object = {
            "classification": "implementation",
            "diagnosis": "구현이 인수 조건을 충족하지 못했습니다.",
            "failed_approaches": ["같은 변경을 검증 없이 반복함"],
            "next_actions": ["실패한 조건을 먼저 재현하고 최소 수정함"],
            "verification_checks": ["인수 명령을 다시 실행함"],
            "candidate_lesson": "실패 조건을 재현한 뒤 최소 수정하고 같은 검증을 다시 실행합니다.",
        }
        self.default_acceptance_behavior: object = 0
        self.change_head_after_worker: str | None = None
        self.codex_version_behavior: object = 0
        self.sandbox_preflight_behavior: object = 0
        self.codex_home_snapshots: list[dict[str, object]] = []

    @staticmethod
    def _next(queue: list[object], default: object) -> object:
        return queue.pop(0) if queue else default

    @staticmethod
    def _result(argv: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""):
        return execute.CommandResult(tuple(argv), returncode, stdout, stderr, 0.01)

    def run(
        self,
        argv,
        *,
        cwd: Path,
        timeout_seconds: int,
        input_text: str | None = None,
        env=None,
    ):
        argv = list(argv)
        environment = dict(env) if env is not None else None
        self.calls.append(
            {
                "argv": argv,
                "cwd": Path(cwd),
                "timeout": timeout_seconds,
                "input": input_text,
                "env": environment,
            }
        )
        if environment and environment.get("CODEX_HOME"):
            home = Path(environment["CODEX_HOME"])
            entries = sorted(path.name for path in home.iterdir())
            self.codex_home_snapshots.append(
                {
                    "argv": argv,
                    "path": str(home),
                    "entries": entries,
                    "mode": stat.S_IMODE(home.stat().st_mode),
                    "home_environment": environment.get("HOME"),
                    "auth_is_symlink": (home / "auth.json").is_symlink(),
                    "auth_target": (
                        os.readlink(home / "auth.json")
                        if (home / "auth.json").is_symlink()
                        else None
                    ),
                    "config": (
                        (home / "config.toml").read_text(encoding="utf-8")
                        if (home / "config.toml").is_file()
                        else None
                    ),
                    "environment_keys": sorted(environment),
                    "temporary_environment": {
                        name: environment.get(name)
                        for name in ("TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME")
                    },
                    "credential_environment": {
                        name: environment.get(name)
                        for name in (
                            "CODEX_API_KEY",
                            "OPENAI_API_KEY",
                            "GITHUB_TOKEN",
                            "SERVICE_SECRET",
                            "DB_PASSWORD",
                            "AWS_PROFILE",
                            "VENDOR_API_KEY",
                            "GOOGLE_APPLICATION_CREDENTIALS",
                            "AZURE_CONFIG_DIR",
                            "CLOUDSDK_CONFIG",
                            "KUBECONFIG",
                            "DOCKER_CONFIG",
                            "SSH_AUTH_SOCK",
                            "GPG_AGENT_INFO",
                            "GNUPGHOME",
                            "PGPASSWORD",
                            "MYSQL_PWD",
                            "GITHUB_PAT",
                            "DATABASE_URL",
                            "SSH_PRIVATE_KEY",
                            "CI_JOB_JWT",
                            "NPM_CONFIG__AUTH",
                            "GIT_ASKPASS",
                        )
                    },
                }
            )
        if argv[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return self._result(argv, stdout=str(self.root) + "\n")
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return self._result(argv, stdout=self.sha + "\n")
        if argv[:2] == ["git", "status"]:
            return self._result(argv, stdout=" M src/example.py\n")
        if argv[:2] == ["git", "diff"]:
            return self._result(argv, stdout="diff --git a/src/example.py b/src/example.py\n")
        if len(argv) >= 2 and argv[1:] == ["--version"]:
            behavior = self.codex_version_behavior
            if isinstance(behavior, BaseException):
                raise behavior
            return self._result(
                argv,
                returncode=int(behavior),
                stdout="codex-cli 0.144.1\n" if not behavior else "",
                stderr="Codex CLI 기동 실패\n" if behavior else "",
            )
        if argv[1:] == ["exec", "--help"]:
            return self._result(
                argv,
                stdout=(
                    "--config --cd --color --ephemeral --ignore-user-config "
                    "--json --output-schema --output-last-message --sandbox\n"
                ),
            )
        if argv[1:] == ["sandbox", "--help"]:
            return self._result(
                argv,
                stdout="-P --sandbox-state-disable-network -C\n",
            )
        if len(argv) >= 2 and argv[1] == "sandbox":
            shell_command = argv[argv.index("-lc") + 1]
            if shell_command == ":":
                behavior = self.sandbox_preflight_behavior
                if isinstance(behavior, BaseException):
                    raise behavior
                return self._result(
                    argv,
                    returncode=int(behavior),
                    stderr="샌드박스 기동 실패\n" if behavior else "",
                )
            behavior = self._next(
                self.acceptance_behaviors, self.default_acceptance_behavior
            )
            if isinstance(behavior, BaseException):
                raise behavior
            if behavior == "no-marker":
                return self._result(
                    argv,
                    returncode=1,
                    stderr="sandbox-exec 기동 실패\n",
                )
            return self._result(
                argv,
                returncode=int(behavior),
                stdout=execute.SANDBOX_STARTED_MARKER + "\nacceptance output\n",
                stderr="acceptance failed\n" if behavior else "",
            )
        if len(argv) >= 2 and argv[1] == "exec":
            final_path = Path(argv[argv.index("--output-last-message") + 1])
            schema_name = Path(argv[argv.index("--output-schema") + 1]).name
            if schema_name.startswith("worker-"):
                behavior = self._next(self.worker_behaviors, self.default_worker_behavior)
            elif schema_name.startswith("verifier-"):
                behavior = self._next(self.verifier_behaviors, self.default_verifier_behavior)
            elif schema_name.startswith("failure-analyst-"):
                behavior = self._next(
                    self.failure_analyst_behaviors,
                    self.default_failure_analyst_behavior,
                )
            else:
                raise AssertionError(f"unexpected schema: {schema_name}")
            if isinstance(behavior, BaseException):
                raise behavior
            if callable(behavior):
                behavior = behavior()
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(behavior, str):
                final_path.write_text(behavior, encoding="utf-8")
            else:
                final_path.write_text(json.dumps(behavior), encoding="utf-8")
            if schema_name.startswith("worker-") and self.change_head_after_worker:
                self.sha = self.change_head_after_worker
            return self._result(argv, stdout='{"type":"turn.completed"}\n')
        raise AssertionError(f"unexpected command: {argv}")


class ProgressObservingRunner(FakeRunner):
    def __init__(self, root: Path, sha: str, run_dir: Path) -> None:
        super().__init__(root, sha)
        self.run_dir = run_dir
        self.progress: list[tuple[str, int | None]] = []

    def run(self, argv, **kwargs):
        index_path = self.run_dir / "index.json"
        if index_path.is_file():
            value = json.loads(index_path.read_text(encoding="utf-8"))
            progress = value.get("progress")
            if isinstance(progress, dict) and isinstance(progress.get("stage"), str):
                current = (progress["stage"], progress.get("attempt"))
                if not self.progress or self.progress[-1] != current:
                    self.progress.append(current)
        return super().run(argv, **kwargs)


class ExecutorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "worktree"
        self.root.mkdir()
        self.run_dir = self.base / "task" / "run"
        self.plan_path = self.base / "plan.json"
        self.sha = "a" * 40

    def write_plan(self, **overrides) -> dict:
        plan = {
            "schema_version": 1,
            "task_id": "task-1",
            "task": "Implement the requested behavior",
            "expected_base_sha": self.sha,
            "steps": [
                {
                    "id": "step-1",
                    "title": "Implement behavior",
                    "instructions": "Edit the implementation and tests.",
                    "acceptance": ["python -m unittest"],
                }
            ],
        }
        plan.update(overrides)
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        return plan

    def execute(self, runner: FakeRunner, **kwargs):
        return execute.execute_plan(
            self.plan_path,
            root=self.root,
            run_dir=self.run_dir,
            runner=runner,
            timeout_seconds=kwargs.pop("timeout_seconds", 60),
            workspace_fingerprinter=kwargs.pop(
                "workspace_fingerprinter", lambda _: "f" * 64
            ),
            **kwargs,
        )

    def read_index(self) -> dict:
        return json.loads((self.run_dir / "index.json").read_text(encoding="utf-8"))

    def read_progress(self) -> dict:
        plan = execute.load_plan(self.plan_path, self.sha)
        return execute.read_progress(
            self.run_dir,
            task_id=plan.task_id,
            plan_sha256=plan.sha256,
            expected_base_sha=self.sha,
            workspace=self.root,
        )

    def codex_calls(self, runner: FakeRunner) -> list[dict[str, object]]:
        return [
            call
            for call in runner.calls
            if call["argv"][1:2] == ["exec"]
            and "--output-schema" in call["argv"]
        ]

    def codex_roles(self, runner: FakeRunner) -> list[str]:
        roles: list[str] = []
        for call in self.codex_calls(runner):
            argv = call["argv"]
            schema = Path(argv[argv.index("--output-schema") + 1]).name
            roles.append(schema.removesuffix("-output-schema.json"))
        return roles

    @staticmethod
    def config_overrides(argv: list[str]) -> list[str]:
        return [
            argv[position + 1]
            for position, value in enumerate(argv[:-1])
            if value == "-c"
        ]

    def acceptance_calls(self, runner: FakeRunner) -> list[dict[str, object]]:
        return [
            call
            for call in runner.calls
            if call["argv"][1:2] == ["sandbox"]
            and execute.SANDBOX_COMMAND_WRAPPER in call["argv"]
        ]

    def acceptance_commands(self, runner: FakeRunner) -> list[str]:
        return [call["argv"][-1] for call in self.acceptance_calls(runner)]

    def test_success_uses_isolated_worker_and_read_only_ephemeral_verifier(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)

        outcome = self.execute(runner)

        self.assertTrue(outcome.ready)
        index = self.read_index()
        self.assertEqual(index["status"], "ready")
        self.assertEqual(index["expected_base_sha"], self.sha)
        self.assertEqual(index["workspace_sha256"], "f" * 64)
        self.assertEqual(index["steps"][0]["attempts"][0]["status"], "passed")
        calls = self.codex_calls(runner)
        self.assertEqual(len(calls), 3)
        worker = calls[0]["argv"]
        verifier = calls[1]["argv"]
        self.assertNotIn("--sandbox", worker)
        self.assertIn("--ephemeral", worker)
        self.assertNotIn("--sandbox", verifier)
        self.assertIn("--ephemeral", verifier)
        for argv in (worker, verifier):
            self.assertIn("--strict-config", argv)
            self.assertIn("--ignore-user-config", argv)
            self.assertNotIn("--ignore-rules", argv)
            self.assertIn("--json", argv)
            self.assertIn("--output-schema", argv)
            self.assertIn("--output-last-message", argv)
            self.assertIn('approval_policy="never"', argv)
            self.assertIn("allow_login_shell=false", argv)
            self.assertIn(
                'shell_environment_policy.inherit="none"', argv
            )
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)
        worker_overrides = self.config_overrides(worker)
        verifier_overrides = self.config_overrides(verifier)
        self.assertIn(
            'default_permissions="scv-nested-worker"', worker_overrides
        )
        self.assertIn(
            'permissions.scv-nested-worker.extends=":workspace"',
            worker_overrides,
        )
        self.assertIn(
            "permissions.scv-nested-worker.network.enabled=false",
            worker_overrides,
        )
        self.assertIn(
            'default_permissions="scv-nested-read-only"', verifier_overrides
        )
        self.assertIn(
            'permissions.scv-nested-read-only.extends=":read-only"',
            verifier_overrides,
        )
        self.assertIn(
            "permissions.scv-nested-read-only.network.enabled=false",
            verifier_overrides,
        )
        acceptance_calls = self.acceptance_calls(runner)
        self.assertEqual(acceptance_calls[0]["argv"][-1], "python -m unittest")
        for call in acceptance_calls:
            argv = call["argv"]
            self.assertEqual(argv[1], "sandbox")
            self.assertIn("--sandbox-state-disable-network", argv)
            self.assertEqual(argv[argv.index("-P") + 1], "scv-acceptance")
            self.assertEqual(argv[argv.index("-C") + 1], str(self.root.resolve()))
        self.assertFalse(
            any(call["argv"][:2] == ["sh", "-lc"] for call in runner.calls)
        )
        evidence = self.run_dir / "evidence" / "step-1" / "attempt-1"
        self.assertTrue((evidence / "worker-final.json").is_file())
        self.assertTrue((evidence / "acceptance.json").is_file())
        self.assertTrue((evidence / "verifier-final.json").is_file())
        self.assertRegex(
            index["steps"][0]["attempts"][0]["evidence_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertRegex(index["final_validation"]["evidence_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual("ready", execute.read_status(self.run_dir)["status"])

    def test_progress_snapshot_is_sanitized_and_readable_while_run_is_locked(self) -> None:
        self.run_dir.mkdir(parents=True)
        index = {
            "schema_version": 1,
            "task_id": "task-1",
            "plan_sha256": "b" * 64,
            "expected_base_sha": self.sha,
            "workspace": str(self.root.resolve()),
            "status": "running",
            "updated_at": "2026-07-13T00:00:00Z",
            "progress": {
                "stage": "failure-analysis",
                "step_id": "step-1",
                "step_position": 1,
                "total_steps": 1,
                "attempt": 1,
                "secret": "must-not-leak",
            },
            "steps": [
                {
                    "id": "step-1",
                    "status": "pending",
                    "attempts": [
                        {
                            "number": 1,
                            "status": "failed",
                            "failure": {"message": "sensitive failure output"},
                        }
                    ],
                    "blockers": [],
                }
            ],
        }
        execute.atomic_write_json(self.run_dir / "index.json", index)

        with execute.run_directory_lock(self.run_dir):
            progress = execute.read_progress(
                self.run_dir,
                task_id="task-1",
                plan_sha256="b" * 64,
                expected_base_sha=self.sha,
                workspace=self.root,
            )

        self.assertEqual(
            {
                "status": "running",
                "stage": "failure-analysis",
                "completed_steps": 0,
                "total_steps": 1,
                "current_step": {
                    "id": "step-1",
                    "position": 1,
                    "total": 1,
                    "status": "pending",
                },
                "attempt": 1,
                "message": "step-1의 1차 실패 원인을 분석하고 있습니다.",
                "updated_at": "2026-07-13T00:00:00Z",
            },
            progress,
        )
        self.assertNotIn("secret", json.dumps(progress))
        self.assertNotIn("sensitive", json.dumps(progress))

        with self.assertRaisesRegex(execute.StateError, "연결이 일치하지 않습니다"):
            execute.read_progress(
                self.run_dir,
                task_id="another-task",
                plan_sha256="b" * 64,
                expected_base_sha=self.sha,
                workspace=self.root,
            )

        index["progress"]["stage"] = "untrusted-stage"
        execute.atomic_write_json(self.run_dir / "index.json", index)
        with self.assertRaisesRegex(execute.StateError, "진행 단계"):
            execute.read_progress(
                self.run_dir,
                task_id="task-1",
                plan_sha256="b" * 64,
                expected_base_sha=self.sha,
                workspace=self.root,
            )

    def test_progress_snapshot_infers_legacy_v1_index_without_mutating_it(self) -> None:
        self.run_dir.mkdir(parents=True)
        index = {
            "schema_version": 1,
            "task_id": "task-1",
            "plan_sha256": "c" * 64,
            "expected_base_sha": self.sha,
            "workspace": str(self.root.resolve()),
            "status": "running",
            "updated_at": "2026-07-13T00:00:00Z",
            "steps": [
                {
                    "id": "step-1",
                    "status": "running",
                    "attempts": [{"number": 1, "status": "running"}],
                    "blockers": [],
                }
            ],
        }
        execute.atomic_write_json(self.run_dir / "index.json", index)
        before = (self.run_dir / "index.json").read_bytes()

        progress = execute.read_progress(
            self.run_dir,
            task_id="task-1",
            plan_sha256="c" * 64,
            expected_base_sha=self.sha,
            workspace=self.root,
        )

        self.assertEqual("worker", progress["stage"])
        self.assertEqual(1, progress["attempt"])
        self.assertEqual(before, (self.run_dir / "index.json").read_bytes())

    def test_progress_summary_covers_every_public_stage(self) -> None:
        cases = {
            "starting": ("running", "pending", None, None, "실행 환경을 준비하고 있습니다."),
            "worker": ("running", "running", "step-1", 1, "step-1의 1차 worker가 구현을 진행하고 있습니다."),
            "acceptance": ("running", "running", "step-1", 1, "step-1의 1차 인수 조건을 검사하고 있습니다."),
            "verifier": ("running", "running", "step-1", 1, "step-1의 1차 결과를 읽기 전용으로 검증하고 있습니다."),
            "failure-analysis": ("running", "pending", "step-1", 1, "step-1의 1차 실패 원인을 분석하고 있습니다."),
            "retry": ("running", "pending", "step-1", 2, "step-1의 2차 재시도를 준비하고 있습니다."),
            "step-complete": ("running", "passed", "step-1", 1, "step-1 단계를 완료했습니다."),
            "final-acceptance": ("running", "passed", None, None, "전체 인수 조건을 다시 검사하고 있습니다."),
            "final-verifier": ("running", "passed", None, None, "전체 결과를 읽기 전용으로 최종 검증하고 있습니다."),
            "complete": ("ready", "passed", None, None, "모든 실행과 검증을 완료했습니다."),
            "blocked": ("blocked", "pending", None, None, "실행 환경 또는 기준 조건 때문에 진행이 차단되었습니다."),
            "failed": ("failed", "failed", None, None, "승인된 실행이 검증을 통과하지 못했습니다."),
            "cancelled": ("cancelled", "cancelled", None, None, "실행이 취소되었습니다."),
        }

        for stage, (status, step_status, step_id, attempt, message) in cases.items():
            with self.subTest(stage=stage):
                progress = {
                    "stage": stage,
                    "step_id": step_id,
                    "step_position": 1 if step_id is not None else None,
                    "total_steps": 1,
                    "attempt": attempt,
                }
                summary = execute.summarize_execution_progress(
                    {
                        "schema_version": 1,
                        "status": status,
                        "updated_at": "2026-07-13T00:00:00Z",
                        "progress": progress,
                        "steps": [
                            {
                                "id": "step-1",
                                "status": step_status,
                                "attempts": [],
                                "blockers": [],
                            }
                        ],
                    }
                )
                self.assertEqual(stage, summary["stage"])
                self.assertEqual(message, summary["message"])

        invalid = {
            "schema_version": 1,
            "status": "ready",
            "progress": {
                "stage": "worker",
                "step_id": "step-1",
                "step_position": 1,
                "total_steps": 1,
                "attempt": 1,
            },
            "steps": [
                {
                    "id": "step-1",
                    "status": "running",
                    "attempts": [],
                    "blockers": [],
                }
            ],
        }
        with self.assertRaisesRegex(execute.StateError, "완료 상태"):
            execute.summarize_execution_progress(invalid)

    def test_progress_reader_rejects_symlink_and_oversized_index(self) -> None:
        self.run_dir.mkdir(parents=True)
        external = self.base / "external-index.json"
        external.write_text("{}", encoding="utf-8")
        os.symlink(external, self.run_dir / "index.json")

        with self.assertRaises(execute.StateError):
            execute.read_progress(
                self.run_dir,
                task_id="task-1",
                plan_sha256="d" * 64,
                expected_base_sha=self.sha,
                workspace=self.root,
            )

        (self.run_dir / "index.json").unlink()
        execute.atomic_write_json(
            self.run_dir / "index.json",
            {
                "schema_version": 1,
                "task_id": "task-1",
                "plan_sha256": "d" * 64,
                "expected_base_sha": self.sha,
                "workspace": str(self.root.resolve()),
                "status": "running",
                "progress": {
                    "stage": "starting",
                    "step_id": None,
                    "step_position": None,
                    "total_steps": 1,
                    "attempt": None,
                },
                "steps": [
                    {
                        "id": "step-1",
                        "status": "pending",
                        "attempts": [],
                        "blockers": [],
                    }
                ],
            },
        )
        with mock.patch.object(execute, "MAX_PROGRESS_INDEX_BYTES", 32):
            with self.assertRaisesRegex(execute.StateError, "허용 크기"):
                execute.read_progress(
                    self.run_dir,
                    task_id="task-1",
                    plan_sha256="d" * 64,
                    expected_base_sha=self.sha,
                    workspace=self.root,
                )

    def test_executor_rejects_symlinked_run_directory(self) -> None:
        self.write_plan()
        external_run = self.base / "external-run"
        external_run.mkdir()
        self.run_dir.parent.mkdir(parents=True)
        self.run_dir.symlink_to(external_run, target_is_directory=True)

        with self.assertRaisesRegex(execute.StateError, "실행 디렉터리"):
            self.execute(FakeRunner(self.root, self.sha))

        self.assertEqual(list(external_run.iterdir()), [])

    def test_executor_rejects_symlinked_index_without_mutating_target(self) -> None:
        self.write_plan()
        self.run_dir.mkdir(parents=True)
        external = self.base / "executor-external-index.json"
        external.write_text('{"schema_version": 1}\n', encoding="utf-8")
        before = external.read_bytes()
        (self.run_dir / "index.json").symlink_to(external)

        with self.assertRaisesRegex(execute.StateError, "실행 인덱스"):
            self.execute(FakeRunner(self.root, self.sha))

        self.assertEqual(external.read_bytes(), before)
        self.assertFalse((self.run_dir / "evidence").exists())

    def test_executor_persists_worker_acceptance_verifier_and_final_progress(self) -> None:
        self.write_plan()
        runner = ProgressObservingRunner(self.root, self.sha, self.run_dir)

        outcome = self.execute(runner)

        self.assertTrue(outcome.ready)
        for stage in (
            "starting",
            "worker",
            "acceptance",
            "verifier",
            "step-complete",
            "final-acceptance",
            "final-verifier",
        ):
            self.assertTrue(
                any(observed_stage == stage for observed_stage, _ in runner.progress),
                runner.progress,
            )
        progress = self.read_progress()
        self.assertEqual("complete", progress["stage"])
        self.assertEqual("ready", progress["status"])
        self.assertEqual(1, progress["completed_steps"])

    def test_workspace_fingerprint_failure_blocks_ready(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)

        def fail_fingerprint(_: Path) -> str:
            raise OSError("지문 수집 실패")

        outcome = self.execute(
            runner,
            workspace_fingerprinter=fail_fingerprint,
        )

        self.assertEqual("blocked", outcome.status)
        index = self.read_index()
        self.assertEqual("blocked", index["status"])
        self.assertNotIn("workspace_sha256", index)
        self.assertIn("지문 수집 실패", index["reason"])
        self.assertEqual("blocked", self.read_progress()["stage"])

    def test_controller_runs_final_acceptance(self) -> None:
        self.write_plan(final_acceptance=["python -m unittest discover"])
        runner = FakeRunner(self.root, self.sha)

        outcome = self.execute(runner)

        self.assertTrue(outcome.ready)
        shell_commands = self.acceptance_commands(runner)
        self.assertEqual(
            shell_commands,
            ["python -m unittest", "python -m unittest", "python -m unittest discover"],
        )
        self.assertEqual(self.read_index()["final_acceptance"]["status"], "passed")

    def test_final_validation_catches_a_later_step_regression(self) -> None:
        self.write_plan(
            steps=[
                {
                    "id": "step-1",
                    "title": "First behavior",
                    "instructions": "Implement the first behavior.",
                    "acceptance": ["check-first"],
                },
                {
                    "id": "step-2",
                    "title": "Second behavior",
                    "instructions": "Implement the second behavior.",
                    "acceptance": ["check-second"],
                },
            ]
        )
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [0, 0, 1]

        outcome = self.execute(runner)

        self.assertEqual("failed", outcome.status)
        index = self.read_index()
        self.assertEqual("failed", index["final_validation"]["status"])
        self.assertRegex(
            index["final_validation"]["evidence_sha256"], r"^[0-9a-f]{64}$"
        )
        self.assertIn("check-first", index["reason"])

        acceptance_path = (
            self.run_dir / index["final_validation"]["evidence"] / "acceptance.json"
        )
        acceptance_path.write_text("[]", encoding="utf-8")
        with self.assertRaises(execute.StateError):
            execute.read_status(self.run_dir)

    def test_ready_run_can_be_explicitly_revalidated_without_worker_replay(self) -> None:
        self.write_plan()
        first_runner = FakeRunner(self.root, self.sha)
        self.assertTrue(self.execute(first_runner).ready)
        second_runner = FakeRunner(self.root, self.sha)

        outcome = self.execute(second_runner, revalidate_ready=True)

        self.assertTrue(outcome.ready)
        codex_calls = self.codex_calls(second_runner)
        self.assertEqual(1, len(codex_calls))
        self.assertNotIn("--sandbox", codex_calls[0]["argv"])
        self.assertIn(
            'default_permissions="scv-nested-read-only"',
            self.config_overrides(codex_calls[0]["argv"]),
        )
        shell_commands = self.acceptance_commands(second_runner)
        self.assertEqual(["python -m unittest"], shell_commands)
        self.assertEqual(2, self.read_index()["final_validation"]["number"])

    def test_legacy_ready_index_without_progress_does_not_trigger_reexecution(self) -> None:
        self.write_plan()
        self.assertTrue(self.execute(FakeRunner(self.root, self.sha)).ready)
        index = self.read_index()
        index.pop("progress")
        execute.atomic_write_json(self.run_dir / "index.json", index)
        runner = FakeRunner(self.root, self.sha)

        outcome = self.execute(runner)

        self.assertTrue(outcome.ready)
        self.assertEqual([], self.codex_calls(runner))
        self.assertNotIn("progress", self.read_index())
        self.assertEqual("complete", self.read_progress()["stage"])

    def test_acceptance_failure_retries_exactly_three_persisted_attempts(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1, 1, 1]

        outcome = self.execute(runner)

        self.assertEqual(outcome.status, "failed")
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual(len(attempts), execute.MAX_ATTEMPTS)
        self.assertTrue(all(attempt["status"] == "failed" for attempt in attempts))
        self.assertTrue(all(attempt["failure"]["stage"] == "acceptance" for attempt in attempts))
        self.assertEqual(len(self.codex_calls(runner)), 3)
        prompts = [call["input"] for call in self.codex_calls(runner)]
        self.assertNotIn("Previous controller failure", prompts[0])
        self.assertIn("Controller retry context", prompts[1])
        progress = self.read_progress()
        self.assertEqual("failed", progress["stage"])
        self.assertEqual(3, progress["attempt"])

    def test_success_path_does_not_invoke_failure_analyst(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)

        outcome = self.execute(runner, learning_root=self.base / "learning")

        self.assertTrue(outcome.ready)
        self.assertEqual(
            ["worker", "verifier", "verifier"], self.codex_roles(runner)
        )

    def test_acceptance_failure_is_analyzed_and_creates_candidate_after_retry(self) -> None:
        self.write_plan()
        runner = ProgressObservingRunner(self.root, self.sha, self.run_dir)
        runner.acceptance_behaviors = [1, 0, 0]
        learning_root = self.base / "learning"

        outcome = self.execute(runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        self.assertEqual(
            ["worker", "failure-analyst", "worker", "verifier", "verifier"],
            self.codex_roles(runner),
        )
        analyst_call = self.codex_calls(runner)[1]
        analyst_argv = analyst_call["argv"]
        self.assertNotIn("--sandbox", analyst_argv)
        self.assertIn(
            'default_permissions="scv-nested-read-only"',
            self.config_overrides(analyst_argv),
        )
        self.assertIn("--ephemeral", analyst_argv)
        self.assertIn("--ignore-user-config", analyst_argv)
        self.assertIn("untrusted data", analyst_call["input"])
        index = self.read_index()
        first, second = index["steps"][0]["attempts"]
        self.assertEqual("analyzed", first["learning"]["status"])
        self.assertRegex(first["evidence_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(
            first["learning"]["analysis_evidence_sha256"], r"^[0-9a-f]{64}$"
        )
        self.assertEqual("candidate-created", second["learning"]["status"])
        retry_prompt = self.codex_calls(runner)[2]["input"]
        self.assertIn("구현이 인수 조건을 충족하지 못했습니다", retry_prompt)
        lessons = execute.LearningStore(learning_root).list_lessons()
        self.assertEqual(1, len(lessons))
        self.assertEqual("candidate", lessons[0]["status"])
        self.assertIn(("failure-analysis", 1), runner.progress)
        self.assertIn(("retry", 2), runner.progress)
        self.assertIn(("worker", 2), runner.progress)

    def test_failure_analyst_prompt_redacts_plan_secrets(self) -> None:
        self.write_plan(
            task="Handle ghp_abcdefghijklmnopqrstuvwxyz123456 safely",
            steps=[
                {
                    "id": "step-1",
                    "title": "Session handling",
                    "instructions": "Reproduce with sessionid=browser-session-secret",
                    "acceptance": ["python -m unittest"],
                }
            ],
        )
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1, 0, 0]

        outcome = self.execute(runner, learning_root=self.base / "learning")

        self.assertTrue(outcome.ready)
        analyst_prompt = [
            call["input"]
            for call, role in zip(self.codex_calls(runner), self.codex_roles(runner))
            if role == "failure-analyst"
        ][0]
        self.assertNotIn("ghp_", analyst_prompt)
        self.assertNotIn("browser-session-secret", analyst_prompt)
        self.assertIn("<redacted>", analyst_prompt)

    def test_controller_classification_creates_scv_repair_proposal(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1, 0, 0]
        runner.failure_analyst_behaviors = [
            {
                **runner.default_failure_analyst_behavior,
                "classification": "controller",
            }
        ]
        learning_root = self.base / "learning"

        outcome = self.execute(runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        first = self.read_index()["steps"][0]["attempts"][0]["learning"]
        self.assertEqual("analyzed", first["status"])
        self.assertRegex(first["proposal_id"], r"^[0-9a-f]{64}$")
        proposals = execute.LearningStore(learning_root).list_proposals()
        self.assertEqual(["controller-defect"], [item["kind"] for item in proposals])

    def test_proposal_write_failure_preserves_controller_analysis(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1, 0, 0]
        runner.failure_analyst_behaviors = [
            {
                **runner.default_failure_analyst_behavior,
                "classification": "controller",
            }
        ]

        with mock.patch.object(
            execute.LearningStore,
            "create_proposal",
            side_effect=OSError("개선 제안 쓰기 실패"),
        ):
            outcome = self.execute(runner, learning_root=self.base / "learning")

        self.assertTrue(outcome.ready)
        first = self.read_index()["steps"][0]["attempts"][0]["learning"]
        self.assertEqual("analyzed", first["status"])
        self.assertRegex(first["observation_id"], r"^[0-9a-f]{64}$")
        self.assertIn("개선 제안 쓰기 실패", first["proposal_unavailable"])

    def test_same_failure_signature_runs_analyst_once_without_scv_repair_proposal(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1, 1, 1]
        learning_root = self.base / "learning"

        outcome = self.execute(runner, learning_root=learning_root)

        self.assertEqual("failed", outcome.status)
        self.assertEqual(
            ["worker", "failure-analyst", "worker", "worker"],
            self.codex_roles(runner),
        )
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual("analyzed", attempts[0]["learning"]["status"])
        self.assertTrue(
            all(
                attempt["learning"]["status"] == "reused"
                for attempt in attempts[1:]
            )
        )
        proposals = execute.LearningStore(learning_root).list_proposals()
        self.assertEqual([], proposals)

    def test_malformed_failure_analyst_falls_back_to_original_retry(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1, 0, 0]
        runner.failure_analyst_behaviors = ["not-json"]

        outcome = self.execute(runner, learning_root=self.base / "learning")

        self.assertTrue(outcome.ready)
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual("unavailable", attempts[0]["learning"]["status"])
        self.assertRegex(
            attempts[0]["learning"]["analysis_evidence_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertNotIn("learning", attempts[1])
        self.assertEqual(2, self.codex_roles(runner).count("worker"))
        self.assertEqual(1, self.codex_roles(runner).count("failure-analyst"))

        analysis_dir = self.run_dir / attempts[0]["learning"]["analysis_evidence"]
        (analysis_dir / "failure-analyst-final-malformed.txt").write_text(
            "tampered", encoding="utf-8"
        )
        with self.assertRaises(execute.StateError):
            execute.read_status(self.run_dir)

    def test_malformed_failure_analyst_is_attempted_once_per_signature(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1, 1, 1]
        runner.failure_analyst_behaviors = ["not-json"]

        outcome = self.execute(runner, learning_root=self.base / "learning")

        self.assertEqual("failed", outcome.status)
        self.assertEqual(1, self.codex_roles(runner).count("failure-analyst"))
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual("unavailable", attempts[0]["learning"]["status"])
        self.assertTrue(attempts[0]["learning"]["analysis_attempted"])
        self.assertEqual(
            ["analysis-skipped", "analysis-skipped"],
            [attempt["learning"]["status"] for attempt in attempts[1:]],
        )

    def test_learning_write_failure_cannot_erase_a_passed_retry(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1, 0, 0]

        with mock.patch.object(
            execute.LearningStore,
            "create_candidate",
            side_effect=OSError("학습 저장소 쓰기 실패"),
        ):
            outcome = self.execute(runner, learning_root=self.base / "learning")

        self.assertTrue(outcome.ready)
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual("passed", attempts[-1]["status"])
        self.assertEqual("unavailable", attempts[-1]["learning"]["status"])
        self.assertIn("학습 저장소 쓰기 실패", attempts[-1]["learning"]["reason"])

    def test_learning_read_failure_degrades_retry_context_without_stalling(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1, 0, 0]

        with mock.patch.object(
            execute.LearningStore,
            "load_observation",
            side_effect=OSError("학습 읽기 실패"),
        ):
            outcome = self.execute(runner, learning_root=self.base / "learning")

        self.assertTrue(outcome.ready)
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual(["failed", "passed"], [item["status"] for item in attempts])
        retry_prompt = [
            call["input"]
            for call, role in zip(self.codex_calls(runner), self.codex_roles(runner))
            if role == "worker"
        ][1]
        self.assertIn("learning_unavailable", retry_prompt)

    def test_unsafe_learning_root_degrades_without_blocking_execution(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        alternate = self.base / "alternate-learning"
        alternate.mkdir()
        learning_root = self.base / "linked-learning"
        os.symlink(alternate, learning_root)

        outcome = self.execute(runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        self.assertEqual("unavailable", self.read_index()["learning"]["status"])

    def test_retry_exhaustion_is_preserved_without_a_proposal(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1, 1, 1]

        outcome = self.execute(runner, learning_root=self.base / "learning")

        self.assertEqual("failed", outcome.status)
        index = self.read_index()
        self.assertEqual("failed", index["status"])
        self.assertIn("최대 시도 횟수", index["reason"])
        self.assertEqual(
            [],
            execute.LearningStore(self.base / "learning").list_proposals(),
        )

    def test_failure_analyst_timeout_does_not_consume_worker_attempt(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1, 0, 0]
        runner.failure_analyst_behaviors = [
            execute.CommandTimeout(["codex", "exec"], 60, "", "분석 시간 초과")
        ]

        outcome = self.execute(runner, learning_root=self.base / "learning")

        self.assertTrue(outcome.ready)
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual(2, len(attempts))
        self.assertEqual("unavailable", attempts[0]["learning"]["status"])
        self.assertEqual(2, self.codex_roles(runner).count("worker"))

    def test_interrupted_failure_analysis_is_sealed_and_not_repeated(self) -> None:
        self.write_plan()
        first_runner = FakeRunner(self.root, self.sha)
        first_runner.acceptance_behaviors = [1]
        first_runner.failure_analyst_behaviors = [SystemExit("analyst crash")]
        learning_root = self.base / "learning"

        with self.assertRaises(SystemExit):
            self.execute(first_runner, learning_root=learning_root)

        second_runner = FakeRunner(self.root, self.sha)
        second_runner.acceptance_behaviors = [0, 0]
        outcome = self.execute(second_runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        attempts = self.read_index()["steps"][0]["attempts"]
        interrupted = attempts[0]["learning"]
        self.assertEqual("unavailable", interrupted["status"])
        self.assertRegex(interrupted["analysis_evidence_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(0, self.codex_roles(second_runner).count("failure-analyst"))

    def test_restart_recovers_failure_saved_before_learning_started(self) -> None:
        self.write_plan()
        learning_root = self.base / "learning"
        first_runner = FakeRunner(self.root, self.sha)
        first_runner.acceptance_behaviors = [1]

        with mock.patch.object(
            execute.StepExecutor,
            "_record_failure_learning",
            side_effect=SystemExit("learning seam crash"),
        ):
            with self.assertRaises(SystemExit):
                self.execute(first_runner, learning_root=learning_root)

        persisted = self.read_index()["steps"][0]["attempts"][0]
        self.assertEqual("failed", persisted["status"])
        self.assertNotIn("learning", persisted)

        second_runner = FakeRunner(self.root, self.sha)
        second_runner.acceptance_behaviors = [0, 0]
        outcome = self.execute(second_runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        self.assertEqual(
            ["failure-analyst", "worker", "verifier", "verifier"],
            self.codex_roles(second_runner),
        )
        first, second = self.read_index()["steps"][0]["attempts"]
        self.assertEqual("analyzed", first["learning"]["status"])
        self.assertEqual("candidate-created", second["learning"]["status"])

    def test_restart_recovers_completed_analyst_without_second_invocation(self) -> None:
        self.write_plan()
        learning_root = self.base / "learning"
        first_runner = FakeRunner(self.root, self.sha)
        first_runner.acceptance_behaviors = [1]

        with mock.patch.object(
            execute.LearningStore,
            "record_observation",
            side_effect=SystemExit("observation seam crash"),
        ):
            with self.assertRaises(SystemExit):
                self.execute(first_runner, learning_root=learning_root)

        self.assertEqual(
            "analysis-running",
            self.read_index()["steps"][0]["attempts"][0]["learning"]["status"],
        )
        second_runner = FakeRunner(self.root, self.sha)
        second_runner.acceptance_behaviors = [0, 0]

        outcome = self.execute(second_runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        self.assertEqual(0, self.codex_roles(second_runner).count("failure-analyst"))
        first, second = self.read_index()["steps"][0]["attempts"]
        self.assertEqual("analyzed", first["learning"]["status"])
        self.assertEqual("candidate-created", second["learning"]["status"])
        self.assertEqual(
            1,
            len(execute.LearningStore(learning_root).list_lessons()),
        )

    def test_restart_reuses_orphan_observation_without_duplicate(self) -> None:
        self.write_plan()
        learning_root = self.base / "learning"
        first_runner = FakeRunner(self.root, self.sha)
        first_runner.acceptance_behaviors = [1]
        original_record = execute.LearningStore.record_observation

        def persist_then_crash(store, failure, analysis, **kwargs):
            original_record(store, failure, analysis, **kwargs)
            raise SystemExit("index link seam crash")

        with mock.patch.object(
            execute.LearningStore,
            "record_observation",
            persist_then_crash,
        ):
            with self.assertRaises(SystemExit):
                self.execute(first_runner, learning_root=learning_root)

        observation_files = list((learning_root / "observations").glob("*.json"))
        self.assertEqual(1, len(observation_files))
        orphan_id = observation_files[0].stem
        second_runner = FakeRunner(self.root, self.sha)
        second_runner.acceptance_behaviors = [0, 0]

        outcome = self.execute(second_runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        self.assertEqual(0, self.codex_roles(second_runner).count("failure-analyst"))
        first = self.read_index()["steps"][0]["attempts"][0]
        self.assertEqual(orphan_id, first["learning"]["observation_id"])
        self.assertEqual(
            1, len(list((learning_root / "observations").glob("*.json")))
        )

    def test_restart_links_orphan_candidate_after_pass_was_persisted(self) -> None:
        self.write_plan()
        learning_root = self.base / "learning"
        first_runner = FakeRunner(self.root, self.sha)
        first_runner.acceptance_behaviors = [1, 0]
        original_success = execute.StepExecutor._record_successful_learning

        def persist_candidate_then_crash(executor, attempt, step_state):
            original_success(executor, attempt, step_state)
            raise SystemExit("candidate index seam crash")

        with mock.patch.object(
            execute.StepExecutor,
            "_record_successful_learning",
            persist_candidate_then_crash,
        ):
            with self.assertRaises(SystemExit):
                self.execute(first_runner, learning_root=learning_root)

        lessons = execute.LearningStore(learning_root).list_lessons()
        self.assertEqual(1, len(lessons))
        orphan_id = lessons[0]["lesson_id"]
        persisted_attempt = self.read_index()["steps"][0]["attempts"][-1]
        self.assertEqual("passed", persisted_attempt["status"])
        self.assertNotIn("learning", persisted_attempt)

        second_runner = FakeRunner(self.root, self.sha)
        second_runner.acceptance_behaviors = [0]
        outcome = self.execute(second_runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        repaired_attempt = self.read_index()["steps"][0]["attempts"][-1]
        self.assertEqual(
            orphan_id, repaired_attempt["learning"]["candidate_lesson_id"]
        )
        self.assertEqual(1, len(execute.LearningStore(learning_root).list_lessons()))

    def test_restart_does_not_count_active_lesson_success_twice(self) -> None:
        self.write_plan()
        learning_root = self.base / "learning"
        seed_runner = FakeRunner(self.root, self.sha)
        seed_runner.acceptance_behaviors = [1, 0, 0]
        self.assertTrue(self.execute(seed_runner, learning_root=learning_root).ready)
        store = execute.LearningStore(learning_root)
        candidate = store.list_lessons(statuses=("candidate",))[0]
        store.approve(
            candidate["lesson_id"],
            approval_evidence={
                "execution_index_sha256": "a" * 64,
                "final_evidence_sha256": "b" * 64,
            },
        )

        self.run_dir = self.base / "task" / "active-success-seam"
        first_runner = FakeRunner(self.root, self.sha)
        first_runner.acceptance_behaviors = [1, 0]
        original_record_success = execute.LearningStore.record_success

        def persist_success_then_crash(store_value, lesson_ids, **kwargs):
            original_record_success(store_value, lesson_ids, **kwargs)
            raise SystemExit("active lesson index seam crash")

        with mock.patch.object(
            execute.LearningStore,
            "record_success",
            persist_success_then_crash,
        ):
            with self.assertRaises(SystemExit):
                self.execute(first_runner, learning_root=learning_root)

        after_crash = store.load_lesson(candidate["lesson_id"])
        self.assertEqual(2, after_crash["validation"]["successful_repair_count"])
        second_runner = FakeRunner(self.root, self.sha)
        second_runner.acceptance_behaviors = [0]

        outcome = self.execute(second_runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        repaired = store.load_lesson(candidate["lesson_id"])
        self.assertEqual(2, repaired["validation"]["successful_repair_count"])
        passed = self.read_index()["steps"][0]["attempts"][-1]
        self.assertEqual("active-lessons-validated", passed["learning"]["status"])

    def test_recurrent_active_lesson_is_marked_suspect_and_stops_injection(self) -> None:
        self.write_plan()
        learning_root = self.base / "learning"
        first_runner = FakeRunner(self.root, self.sha)
        first_runner.acceptance_behaviors = [1, 0, 0]
        self.assertTrue(
            self.execute(first_runner, learning_root=learning_root).ready
        )
        learning_store = execute.LearningStore(learning_root)
        candidate = learning_store.list_lessons(statuses=("candidate",))[0]
        learning_store.approve(
            candidate["lesson_id"],
            approval_evidence={
                "execution_index_sha256": "a" * 64,
                "final_evidence_sha256": "b" * 64,
            },
        )

        self.run_dir = self.base / "task" / "recurrence-run"
        second_runner = FakeRunner(self.root, self.sha)
        second_runner.acceptance_behaviors = [1, 1, 0, 0]
        outcome = self.execute(second_runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        worker_calls = [
            call
            for call, role in zip(
                self.codex_calls(second_runner), self.codex_roles(second_runner)
            )
            if role == "worker"
        ]
        self.assertNotIn("validated_lessons", worker_calls[0]["input"])
        self.assertIn("validated_lessons", worker_calls[1]["input"])
        self.assertNotIn("validated_lessons", worker_calls[2]["input"])
        self.assertEqual(
            "suspect", learning_store.load_lesson(candidate["lesson_id"])["status"]
        )

    def test_recurrent_lesson_stops_injection_when_suspect_write_is_unavailable(self) -> None:
        self.write_plan()
        learning_root = self.base / "learning"
        first_runner = FakeRunner(self.root, self.sha)
        first_runner.acceptance_behaviors = [1, 0, 0]
        self.assertTrue(self.execute(first_runner, learning_root=learning_root).ready)
        learning_store = execute.LearningStore(learning_root)
        candidate = learning_store.list_lessons(statuses=("candidate",))[0]
        learning_store.approve(
            candidate["lesson_id"],
            approval_evidence={
                "execution_index_sha256": "a" * 64,
                "final_evidence_sha256": "b" * 64,
            },
        )

        self.run_dir = self.base / "task" / "suspect-write-unavailable-run"
        second_runner = FakeRunner(self.root, self.sha)
        second_runner.acceptance_behaviors = [1, 1, 0, 0]
        with mock.patch.object(
            execute.LearningStore,
            "mark_suspect",
            return_value=[],
        ):
            outcome = self.execute(second_runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        worker_prompts = [
            call["input"]
            for call, role in zip(
                self.codex_calls(second_runner), self.codex_roles(second_runner)
            )
            if role == "worker"
        ]
        self.assertNotIn("validated_lessons", worker_prompts[0])
        self.assertIn("validated_lessons", worker_prompts[1])
        self.assertNotIn("validated_lessons", worker_prompts[2])
        self.assertEqual(
            "active", learning_store.load_lesson(candidate["lesson_id"])["status"]
        )

    def test_uninjected_lesson_is_not_marked_suspect(self) -> None:
        self.write_plan()
        learning_root = self.base / "learning"
        first_runner = FakeRunner(self.root, self.sha)
        first_runner.acceptance_behaviors = [1, 0, 0]
        self.assertTrue(self.execute(first_runner, learning_root=learning_root).ready)
        learning_store = execute.LearningStore(learning_root)
        candidate = learning_store.list_lessons(statuses=("candidate",))[0]
        learning_store.approve(
            candidate["lesson_id"],
            approval_evidence={
                "execution_index_sha256": "a" * 64,
                "final_evidence_sha256": "b" * 64,
            },
        )

        self.run_dir = self.base / "task" / "transient-lesson-read-run"
        second_runner = FakeRunner(self.root, self.sha)
        second_runner.acceptance_behaviors = [1, 1, 0, 0]
        original_load = execute.LearningStore.load_lesson
        calls = 0

        def fail_once(store, lesson_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("lesson 일시 읽기 실패")
            return original_load(store, lesson_id)

        with mock.patch.object(execute.LearningStore, "load_lesson", fail_once):
            outcome = self.execute(second_runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        self.assertEqual(
            "active", learning_store.load_lesson(candidate["lesson_id"])["status"]
        )
        worker_prompts = [
            call["input"]
            for call, role in zip(
                self.codex_calls(second_runner), self.codex_roles(second_runner)
            )
            if role == "worker"
        ]
        self.assertNotIn("validated_lessons", worker_prompts[1])
        self.assertIn("validated_lessons", worker_prompts[2])

    def test_failed_and_analysis_evidence_tampering_is_detected(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1, 0, 0]
        self.assertTrue(
            self.execute(runner, learning_root=self.base / "learning").ready
        )
        index = self.read_index()
        first = index["steps"][0]["attempts"][0]
        failure_dir = self.run_dir / first["evidence"]
        (failure_dir / "acceptance.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(execute.StateError):
            execute.read_status(self.run_dir)

        runner = FakeRunner(self.root, self.sha)
        self.run_dir = self.base / "task" / "second-run"
        runner.acceptance_behaviors = [1, 0, 0]
        self.assertTrue(
            self.execute(runner, learning_root=self.base / "second-learning").ready
        )
        second_index = self.read_index()
        analysis = second_index["steps"][0]["attempts"][0]["learning"]
        analysis_dir = self.run_dir / analysis["analysis_evidence"]
        (analysis_dir / "failure-analyst-final.json").write_text(
            '{"classification":"unknown"}\n', encoding="utf-8"
        )
        with self.assertRaises(execute.StateError):
            execute.read_status(self.run_dir)

    def test_malformed_worker_output_is_evidence_and_never_success(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.default_worker_behavior = "not-json"

        outcome = self.execute(runner)

        self.assertEqual(outcome.status, "failed")
        index = self.read_index()
        self.assertEqual(len(index["steps"][0]["attempts"]), 3)
        self.assertTrue(
            all(
                attempt["failure"]["stage"] == "worker"
                for attempt in index["steps"][0]["attempts"]
            )
        )
        malformed = self.run_dir / "evidence" / "step-1" / "attempt-1" / "worker-final-malformed.txt"
        self.assertEqual(malformed.read_text(encoding="utf-8"), "not-json")

    def test_worker_cannot_add_a_success_claim(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.default_worker_behavior = {
            "summary": "done",
            "changed_files": [],
            "tests_run": [],
            "risks": [],
            "success": True,
        }

        outcome = self.execute(runner)

        self.assertEqual(outcome.status, "failed")
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertTrue(all(attempt["failure"]["stage"] == "worker" for attempt in attempts))
        self.assertFalse(
            self.acceptance_calls(runner)
        )

    def test_verifier_failure_is_retried_then_may_pass(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.verifier_behaviors = [
            {"verdict": "fail", "summary": "missing edge case", "findings": ["add test"]},
            {"verdict": "pass", "summary": "fixed", "findings": []},
        ]

        outcome = self.execute(runner)

        self.assertTrue(outcome.ready)
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual([attempt["status"] for attempt in attempts], ["failed", "passed"])
        self.assertEqual(attempts[0]["failure"]["stage"], "verifier")

    def test_worker_timeout_is_persisted_and_bounded(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.default_worker_behavior = execute.CommandTimeout(
            ["codex", "exec"], 60, '{"type":"partial"}\n', "timed out"
        )

        outcome = self.execute(runner)

        self.assertEqual(outcome.status, "failed")
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual(len(attempts), 3)
        self.assertTrue(all(attempt["status"] == "timed_out" for attempt in attempts))
        events = self.run_dir / "evidence" / "step-1" / "attempt-1" / "worker-events.jsonl"
        self.assertIn("partial", events.read_text(encoding="utf-8"))

    def test_acceptance_timeout_is_not_analyzed_or_learned(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [
            execute.CommandTimeout(
                ["codex", "sandbox"],
                60,
                execute.SANDBOX_STARTED_MARKER + "\npartial acceptance\n",
                "acceptance timed out",
            ),
            0,
            0,
        ]
        learning_root = self.base / "learning"

        outcome = self.execute(runner, learning_root=learning_root)

        self.assertTrue(outcome.ready)
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual(["timed_out", "passed"], [item["status"] for item in attempts])
        acceptance = json.loads(
            (
                self.run_dir
                / attempts[0]["evidence"]
                / "acceptance.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual("timed_out", acceptance[-1]["status"])
        self.assertNotIn("learning", attempts[0])
        self.assertEqual(0, self.codex_roles(runner).count("failure-analyst"))
        learning_store = execute.LearningStore(learning_root)
        self.assertEqual([], learning_store.list_lessons())
        self.assertEqual([], learning_store.list_proposals())

    def test_cancellation_stops_without_consuming_more_attempts(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.worker_behaviors = [execute.CommandCancelled("stop")]

        with self.assertRaises(execute.CommandCancelled):
            self.execute(runner)

        index = self.read_index()
        self.assertEqual(index["status"], "cancelled")
        self.assertEqual(len(index["steps"][0]["attempts"]), 1)
        self.assertEqual(index["steps"][0]["attempts"][0]["status"], "cancelled")
        self.assertEqual("cancelled", self.read_progress()["stage"])

    def test_initial_base_mismatch_blocks_before_codex(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, "b" * 40)

        with self.assertRaises(execute.BaseMismatchError):
            self.execute(runner)

        self.assertFalse(self.run_dir.joinpath("index.json").exists())
        self.assertEqual(self.codex_calls(runner), [])

    def test_worker_changing_head_blocks_without_reset_or_push(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.change_head_after_worker = "b" * 40

        with self.assertRaises(execute.BaseMismatchError):
            self.execute(runner)

        index = self.read_index()
        self.assertEqual(index["status"], "blocked")
        self.assertEqual(len(index["steps"][0]["attempts"]), 1)
        self.assertRegex(
            index["steps"][0]["attempts"][0]["evidence_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertEqual("blocked", execute.read_status(self.run_dir)["status"])
        self.assertEqual("blocked", self.read_progress()["stage"])
        flattened = [part for call in runner.calls for part in call["argv"]]
        self.assertNotIn("push", flattened)
        self.assertNotIn("reset", flattened)

    def test_interrupted_attempt_is_consumed_and_resume_uses_next_attempt(self) -> None:
        self.write_plan()
        plan = execute.load_plan(self.plan_path)
        self.run_dir.mkdir(parents=True)
        execute.atomic_write_json(
            self.run_dir / "index.json",
            {
                "schema_version": 1,
                "task_id": plan.task_id,
                "plan_sha256": plan.sha256,
                "expected_base_sha": self.sha,
                "workspace": str(self.root.resolve()),
                "status": "running",
                "created_at": "earlier",
                "updated_at": "earlier",
                "completed_at": None,
                "final_acceptance": None,
                "steps": [
                    {
                        "id": "step-1",
                        "status": "running",
                        "attempts": [
                            {
                                "number": 1,
                                "status": "running",
                                "started_at": "earlier",
                                "finished_at": None,
                                "failure": None,
                                "evidence": "evidence/step-1/attempt-1",
                            }
                        ],
                    }
                ],
            },
        )
        runner = FakeRunner(self.root, self.sha)

        outcome = self.execute(runner)

        self.assertTrue(outcome.ready)
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual([attempt["status"] for attempt in attempts], ["interrupted", "passed"])
        self.assertEqual(attempts[1]["number"], 2)

    def test_plan_change_cannot_reuse_existing_state(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        self.assertTrue(self.execute(runner).ready)
        self.write_plan(task="A different task")

        with self.assertRaises(execute.StateError):
            self.execute(FakeRunner(self.root, self.sha))

    def test_expected_base_override_must_match_plan(self) -> None:
        self.write_plan()
        with self.assertRaises(execute.PlanError):
            execute.load_plan(self.plan_path, "b" * 40)

    def test_plan_without_base_freezes_cli_override(self) -> None:
        plan = self.write_plan()
        plan.pop("expected_base_sha")
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        runner = FakeRunner(self.root, self.sha)

        outcome = self.execute(runner, expected_base=self.sha)

        self.assertTrue(outcome.ready)
        self.assertEqual(self.read_index()["expected_base_sha"], self.sha)

    def test_git_push_acceptance_is_rejected_including_git_c_option(self) -> None:
        for command in ("git push", "git -C . push origin main", "test -f x && git push"):
            with self.subTest(command=command):
                self.write_plan(
                    steps=[
                        {
                            "id": "step-1",
                            "title": "Unsafe",
                            "instructions": "Do it",
                            "acceptance": [command],
                        }
                    ]
                )
                with self.assertRaises(execute.PlanError):
                    execute.load_plan(self.plan_path)

    def test_acceptance_timeout_is_recorded_before_retry(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [
            execute.CommandTimeout(
                ["codex", "sandbox"],
                60,
                execute.SANDBOX_STARTED_MARKER + "\npartial",
                "",
            ),
            0,
        ]

        outcome = self.execute(runner)

        self.assertTrue(outcome.ready)
        first = self.run_dir / "evidence" / "step-1" / "attempt-1" / "acceptance.json"
        self.assertEqual(json.loads(first.read_text(encoding="utf-8"))[0]["status"], "timed_out")

    def test_acceptance_and_nested_codex_use_minimal_temporary_homes(self) -> None:
        self.write_plan()
        git_common = self.base / "repository.git"
        git_dir = git_common / "worktrees" / "worktree"
        git_dir.mkdir(parents=True)
        (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
        (self.root / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
        user_home = self.base / "user-codex"
        user_home.mkdir()
        (user_home / "AGENTS.md").write_text("사용자 지침", encoding="utf-8")
        (user_home / "skills").mkdir()
        (user_home / "auth.json").write_text('{"token":"test"}', encoding="utf-8")
        runner = FakeRunner(self.root, self.sha)

        credentials = {
            "CODEX_HOME": str(user_home),
            "CODEX_API_KEY": "codex-key",
            "OPENAI_API_KEY": "openai-key",
            "GITHUB_TOKEN": "github-token",
            "SERVICE_SECRET": "service-secret",
            "DB_PASSWORD": "db-password",
            "AWS_PROFILE": "production",
            "VENDOR_API_KEY": "vendor-key",
            "GOOGLE_APPLICATION_CREDENTIALS": "/secrets/google.json",
            "AZURE_CONFIG_DIR": "/secrets/azure",
            "CLOUDSDK_CONFIG": "/secrets/gcloud",
            "KUBECONFIG": "/secrets/kubeconfig",
            "DOCKER_CONFIG": "/secrets/docker",
            "SSH_AUTH_SOCK": "/secrets/ssh.sock",
            "GPG_AGENT_INFO": "/secrets/gpg",
            "GNUPGHOME": "/secrets/gnupg",
            "PGPASSWORD": "postgres-password",
            "MYSQL_PWD": "mysql-password",
            "GITHUB_PAT": "github-pat",
            "DATABASE_URL": "postgres://secret@example.invalid/database",
            "SSH_PRIVATE_KEY": "private-key-material",
            "CI_JOB_JWT": "job-jwt",
            "NPM_CONFIG__AUTH": "npm-auth",
            "GIT_ASKPASS": "/secrets/askpass",
        }
        with mock.patch.dict(os.environ, credentials):
            outcome = self.execute(runner)

        self.assertTrue(outcome.ready)
        nested = [
            snapshot
            for snapshot in runner.codex_home_snapshots
            if snapshot["argv"][1:2] == ["exec"]
            and "--output-schema" in snapshot["argv"]
        ]
        self.assertEqual(3, len(nested))
        for snapshot in nested:
            self.assertEqual(["auth.json"], snapshot["entries"])
            self.assertEqual(0o700, snapshot["mode"])
            self.assertTrue(snapshot["auth_is_symlink"])
            self.assertEqual(str((user_home / "auth.json").resolve()), snapshot["auth_target"])
            self.assertNotEqual(str(user_home), snapshot["path"])
            self.assertEqual(snapshot["path"], snapshot["home_environment"])
            self.assertFalse(Path(snapshot["path"]).exists())
            self.assertIn(execute.NESTED_SHELL_EXCLUDE_OVERRIDE, snapshot["argv"])
            self.assertTrue(
                all(
                    value is None
                    for value in snapshot["credential_environment"].values()
                )
            )
            self.assertNotIn("--sandbox", snapshot["argv"])
            overrides = self.config_overrides(snapshot["argv"])
            for secret_name in (
                "PGPASSWORD",
                "MYSQL_PWD",
                "GITHUB_PAT",
                "DATABASE_URL",
                "SSH_PRIVATE_KEY",
                "CI_JOB_JWT",
                "NPM_CONFIG__AUTH",
                "GIT_ASKPASS",
            ):
                self.assertFalse(
                    any(
                        value.startswith(
                            f"shell_environment_policy.set.{secret_name}="
                        )
                        for value in overrides
                    )
                )
            filesystem = next(
                value for value in overrides if ".filesystem={" in value
            )
            self.assertIn(
                json.dumps(str(Path(snapshot["path"]) / "auth.json")),
                filesystem,
            )
            self.assertIn(
                json.dumps(str((user_home / "auth.json").resolve())),
                filesystem,
            )
            self.assertIn(
                f'{json.dumps(str(user_home.resolve()))}="deny"',
                filesystem,
            )
            self.assertIn(
                f'{json.dumps(str(git_dir.resolve()))}="read"',
                filesystem,
            )
            self.assertIn(
                f'{json.dumps(str(git_common.resolve()))}="read"',
                filesystem,
            )
            profile = next(
                value
                for value in overrides
                if value.startswith('default_permissions="scv-nested-')
            )
            workspace_permission = (
                "write" if "worker" in profile else "read"
            )
            self.assertIn(
                f'{json.dumps(str(self.root.resolve()))}='
                f'{json.dumps(workspace_permission)}',
                filesystem,
            )
            for sensitive in (
                execute._real_user_home() / ".gitconfig",
                execute._real_user_home() / ".ssh",
                execute._real_user_home() / ".aws",
            ):
                self.assertIn(
                    f'{json.dumps(str(sensitive.resolve()))}="deny"',
                    filesystem,
                )
            shell_home_override = next(
                value
                for value in overrides
                if value.startswith("shell_environment_policy.set.HOME=")
            )
            shell_home = Path(json.loads(shell_home_override.split("=", 1)[1]))
            self.assertNotEqual(Path(snapshot["path"]), shell_home)
            self.assertIn(json.dumps(str(shell_home)), filesystem)
            self.assertFalse(shell_home.exists())
            allowed_parent_keys = set(execute.NESTED_PARENT_SAFE_ENVIRONMENT) | {
                "CODEX_HOME",
                "HOME",
                "TMPDIR",
                "TMP",
                "TEMP",
                "XDG_CACHE_HOME",
            }
            self.assertLessEqual(
                set(snapshot["environment_keys"]), allowed_parent_keys
            )

        acceptance = [
            snapshot
            for snapshot in runner.codex_home_snapshots
            if snapshot["argv"][1:2] == ["sandbox"]
            and "--" in snapshot["argv"]
        ]
        self.assertGreaterEqual(len(acceptance), 3)
        for snapshot in acceptance:
            self.assertEqual(["config.toml"], snapshot["entries"])
            self.assertEqual(0o700, snapshot["mode"])
            config = snapshot["config"]
            self.assertIn('default_permissions = "scv-acceptance"', config)
            self.assertIn(
                '[permissions.scv-acceptance.filesystem.":workspace_roots"]',
                config,
            )
            self.assertIn("[permissions.scv-acceptance.workspace_roots]", config)
            self.assertIn(
                f'{json.dumps(str(self.root.resolve()))} = "write"',
                config,
            )
            self.assertIn(
                f'{json.dumps(str(git_dir.resolve()))} = "read"',
                config,
            )
            self.assertIn(
                f'{json.dumps(str(git_common.resolve()))} = "read"',
                config,
            )
            self.assertIn(
                f'{json.dumps(str(user_home.resolve()))} = "deny"',
                config,
            )
            self.assertIn(
                f'{json.dumps(str((execute._real_user_home() / ".gitconfig").resolve()))} = "deny"',
                config,
            )
            temporary_paths = set(snapshot["temporary_environment"].values())
            self.assertEqual(1, len(temporary_paths))
            scratch = Path(temporary_paths.pop())
            self.assertEqual(str(scratch), snapshot["home_environment"])
            self.assertIn(self.run_dir.resolve(), scratch.parents)
            self.assertIn(json.dumps(str(scratch), ensure_ascii=False), config)
            self.assertTrue(
                all(value is None for value in snapshot["credential_environment"].values())
            )
            allowed_acceptance_keys = set(
                execute.ACCEPTANCE_PARENT_SAFE_ENVIRONMENT
            ) | {
                "CODEX_HOME",
                "HOME",
                "TMPDIR",
                "TMP",
                "TEMP",
                "XDG_CACHE_HOME",
            }
            self.assertLessEqual(
                set(snapshot["environment_keys"]),
                allowed_acceptance_keys,
            )
            self.assertFalse(scratch.exists())
            self.assertFalse(Path(snapshot["path"]).exists())

    def test_api_key_only_auth_is_parent_only_and_shell_excluded(self) -> None:
        self.write_plan()
        user_home = self.base / "api-key-only-codex"
        user_home.mkdir()
        runner = FakeRunner(self.root, self.sha)
        environment = {
            "CODEX_HOME": str(user_home),
            "CODEX_API_KEY": "codex-parent-auth",
            "OPENAI_API_KEY": "unused-parent-auth",
            "GITHUB_TOKEN": "unrelated-token",
            "SERVICE_SECRET": "unrelated-secret",
        }

        with mock.patch.dict(os.environ, environment):
            outcome = self.execute(runner)

        self.assertTrue(outcome.ready)
        nested = [
            snapshot
            for snapshot in runner.codex_home_snapshots
            if snapshot["argv"][1:2] == ["exec"]
            and "--output-schema" in snapshot["argv"]
        ]
        self.assertEqual(3, len(nested))
        for snapshot in nested:
            credentials = snapshot["credential_environment"]
            self.assertEqual("codex-parent-auth", credentials["CODEX_API_KEY"])
            self.assertIsNone(credentials["OPENAI_API_KEY"])
            self.assertIsNone(credentials["GITHUB_TOKEN"])
            self.assertIsNone(credentials["SERVICE_SECRET"])
            self.assertNotIn("auth.json", snapshot["entries"])
            overrides = self.config_overrides(snapshot["argv"])
            self.assertIn(
                "shell_environment_policy.ignore_default_excludes=false",
                overrides,
            )
            self.assertIn(execute.NESTED_SHELL_EXCLUDE_OVERRIDE, overrides)
            self.assertFalse(
                any(
                    value.startswith(
                        "shell_environment_policy.set.CODEX_API_KEY="
                    )
                    for value in overrides
                )
            )

    def test_sandbox_preflight_failure_blocks_without_consuming_attempt(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.sandbox_preflight_behavior = 1

        outcome = self.execute(runner)

        self.assertEqual("blocked", outcome.status)
        index = self.read_index()
        self.assertEqual([], index["steps"][0]["attempts"])
        self.assertEqual([], index["steps"][0]["blockers"])
        self.assertIn("샌드박스", index["reason"])
        self.assertEqual([], self.codex_calls(runner))
        self.assertEqual("blocked", self.read_progress()["stage"])

    def test_unhashable_failure_evidence_becomes_non_consuming_blocker(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = [1]
        original_hash = execute.hash_evidence_directory

        def fail_first_attempt(directory: Path) -> str:
            if directory.name == "attempt-1":
                raise execute.StateError("실패 증거 해시 실패")
            return original_hash(directory)

        with mock.patch.object(
            execute, "hash_evidence_directory", side_effect=fail_first_attempt
        ):
            outcome = self.execute(runner)

        self.assertEqual("blocked", outcome.status)
        index = self.read_index()
        self.assertEqual([], index["steps"][0]["attempts"])
        self.assertEqual(1, len(index["steps"][0]["blockers"]))
        self.assertIn(
            "실패 증거", index["steps"][0]["blockers"][0]["failure"]["message"]
        )

        resumed_runner = FakeRunner(self.root, self.sha)
        resumed_runner.acceptance_behaviors = [0, 0]
        self.assertTrue(self.execute(resumed_runner).ready)

    def test_worker_launch_failure_is_a_non_consuming_blocker_and_can_resume(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.worker_behaviors = [execute.CommandLaunchError("codex 실행 파일 없음")]

        outcome = self.execute(runner)

        self.assertEqual("blocked", outcome.status)
        step = self.read_index()["steps"][0]
        self.assertEqual([], step["attempts"])
        self.assertEqual(1, len(step["blockers"]))
        self.assertEqual("infrastructure", step["blockers"][0]["failure"]["stage"])

        resumed = self.execute(FakeRunner(self.root, self.sha))

        self.assertTrue(resumed.ready)
        step = self.read_index()["steps"][0]
        self.assertEqual([1], [attempt["number"] for attempt in step["attempts"]])
        self.assertEqual("passed", step["attempts"][0]["status"])
        self.assertIn("attempt-1-run-2", step["attempts"][0]["evidence"])

    def test_crash_before_worker_launch_marker_does_not_consume_attempt(self) -> None:
        self.write_plan()
        for _ in range(3):
            crashing_runner = FakeRunner(self.root, self.sha)
            with mock.patch.object(
                execute.StepExecutor,
                "_build_retry_context",
                side_effect=SystemExit("pre-launch crash"),
            ):
                with self.assertRaises(SystemExit):
                    self.execute(crashing_runner)

            pending = self.read_index()["steps"][0]["attempts"]
            self.assertEqual(1, len(pending))
            self.assertEqual("running", pending[0]["status"])
            self.assertEqual("pending", pending[0]["worker_launch"]["status"])
            self.assertEqual([], self.codex_calls(crashing_runner))

        outcome = self.execute(FakeRunner(self.root, self.sha))

        self.assertTrue(outcome.ready)
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual([1], [attempt["number"] for attempt in attempts])
        self.assertEqual(["passed"], [attempt["status"] for attempt in attempts])
        self.assertEqual("launched", attempts[0]["worker_launch"]["status"])

    def test_crash_after_worker_launch_marker_consumes_attempt(self) -> None:
        self.write_plan()
        first_runner = FakeRunner(self.root, self.sha)

        with mock.patch.object(
            execute.StepExecutor,
            "_run_worker",
            side_effect=SystemExit("post-launch crash"),
        ):
            with self.assertRaises(SystemExit):
                self.execute(first_runner)

        launched = self.read_index()["steps"][0]["attempts"][0]
        self.assertEqual("running", launched["status"])
        self.assertEqual("launched", launched["worker_launch"]["status"])

        outcome = self.execute(FakeRunner(self.root, self.sha))

        self.assertTrue(outcome.ready)
        attempts = self.read_index()["steps"][0]["attempts"]
        self.assertEqual([1, 2], [attempt["number"] for attempt in attempts])
        self.assertEqual(
            ["interrupted", "passed"],
            [attempt["status"] for attempt in attempts],
        )

    def test_missing_sandbox_start_marker_is_a_non_consuming_blocker(self) -> None:
        self.write_plan()
        runner = FakeRunner(self.root, self.sha)
        runner.acceptance_behaviors = ["no-marker"]

        outcome = self.execute(runner)

        self.assertEqual("blocked", outcome.status)
        step = self.read_index()["steps"][0]
        self.assertEqual([], step["attempts"])
        self.assertEqual(1, len(step["blockers"]))
        evidence = self.run_dir / step["blockers"][0]["evidence"] / "acceptance.json"
        self.assertEqual("blocked", json.loads(evidence.read_text(encoding="utf-8"))[0]["status"])

    def test_acceptance_126_and_127_are_non_consuming_infrastructure_blockers(self) -> None:
        self.write_plan()
        for returncode in (126, 127):
            with self.subTest(returncode=returncode):
                self.run_dir = self.base / f"task-{returncode}" / "run"
                runner = FakeRunner(self.root, self.sha)
                runner.acceptance_behaviors = [returncode]

                outcome = self.execute(runner)

                self.assertEqual("blocked", outcome.status)
                step = self.read_index()["steps"][0]
                self.assertEqual([], step["attempts"])
                self.assertEqual(1, len(step["blockers"]))
                self.assertIn(
                    f"종료 코드 {returncode}",
                    step["blockers"][0]["failure"]["message"],
                )
                evidence = (
                    self.run_dir
                    / step["blockers"][0]["evidence"]
                    / "acceptance.json"
                )
                record = json.loads(evidence.read_text(encoding="utf-8"))[0]
                self.assertEqual("blocked", record["status"])
                self.assertEqual(returncode, record["returncode"])

    @unittest.skipUnless(os.name == "posix", "POSIX 파일 잠금 동작을 검증합니다")
    def test_concurrent_executor_is_rejected_without_touching_the_index(self) -> None:
        self.write_plan()
        self.assertTrue(self.execute(FakeRunner(self.root, self.sha)).ready)
        before = (self.run_dir / "index.json").read_bytes()
        descriptor = os.open(self.run_dir / execute.RUN_LOCK_NAME, os.O_RDWR)
        execute.fcntl.flock(descriptor, execute.fcntl.LOCK_EX | execute.fcntl.LOCK_NB)
        runner = FakeRunner(self.root, self.sha)
        try:
            with self.assertRaises(execute.ExecutionBusy):
                self.execute(runner)
        finally:
            execute.fcntl.flock(descriptor, execute.fcntl.LOCK_UN)
            os.close(descriptor)

        self.assertEqual(before, (self.run_dir / "index.json").read_bytes())
        self.assertEqual([], runner.calls)

    def test_status_rejects_tampered_passed_evidence(self) -> None:
        self.write_plan()
        self.assertTrue(self.execute(FakeRunner(self.root, self.sha)).ready)
        index = self.read_index()
        evidence = self.run_dir / index["steps"][0]["attempts"][0]["evidence"]
        (evidence / "worker-final.json").write_text(
            '{"summary":"변조됨"}\n', encoding="utf-8"
        )

        with self.assertRaisesRegex(execute.StateError, "SHA-256"):
            execute.read_status(self.run_dir)

    def test_status_rejects_evidence_path_escape(self) -> None:
        self.write_plan()
        self.assertTrue(self.execute(FakeRunner(self.root, self.sha)).ready)
        index = self.read_index()
        index["steps"][0]["attempts"][0]["evidence"] = "../outside"
        execute.atomic_write_json(self.run_dir / "index.json", index)

        with self.assertRaisesRegex(execute.StateError, "상대 경로"):
            execute.read_status(self.run_dir)

    @unittest.skipUnless(hasattr(os, "symlink"), "심볼릭 링크 동작을 검증합니다")
    def test_status_rejects_symlink_inside_passed_evidence(self) -> None:
        self.write_plan()
        self.assertTrue(self.execute(FakeRunner(self.root, self.sha)).ready)
        index = self.read_index()
        evidence = self.run_dir / index["steps"][0]["attempts"][0]["evidence"]
        target = self.base / "outside.txt"
        target.write_text("외부 내용", encoding="utf-8")
        os.symlink(target, evidence / "outside-link")

        with self.assertRaisesRegex(execute.StateError, "심볼릭 링크"):
            execute.read_status(self.run_dir)

    def test_status_rejects_missing_ready_final_evidence(self) -> None:
        self.write_plan()
        self.assertTrue(self.execute(FakeRunner(self.root, self.sha)).ready)
        index = self.read_index()
        final_evidence = self.run_dir / index["final_validation"]["evidence"]
        final_evidence.rename(self.base / "moved-final-evidence")

        with self.assertRaisesRegex(execute.StateError, "없습니다"):
            execute.read_status(self.run_dir)

    def test_passed_step_without_a_real_passed_attempt_is_rejected(self) -> None:
        self.write_plan()
        self.assertTrue(self.execute(FakeRunner(self.root, self.sha)).ready)
        index = self.read_index()
        attempt = index["steps"][0]["attempts"][0]
        attempt["status"] = "failed"
        attempt["failure"] = {"stage": "tampered", "message": "통과 기록 제거"}
        execute.atomic_write_json(self.run_dir / "index.json", index)

        with self.assertRaises(execute.StateError):
            self.execute(FakeRunner(self.root, self.sha))

    def test_non_sequential_attempt_numbers_are_rejected(self) -> None:
        self.write_plan()
        self.assertTrue(self.execute(FakeRunner(self.root, self.sha)).ready)
        index = self.read_index()
        index["steps"][0]["attempts"][0]["number"] = 2
        execute.atomic_write_json(self.run_dir / "index.json", index)

        with self.assertRaises(execute.StateError):
            self.execute(FakeRunner(self.root, self.sha))


class CommandRunnerTest(unittest.TestCase):
    def test_subprocess_is_non_shell_and_captured(self) -> None:
        process = mock.Mock()
        process.communicate.return_value = ("ok", "")
        process.returncode = 0
        with mock.patch.object(execute.subprocess, "Popen", return_value=process) as popen:
            result = execute.CommandRunner().run(
                ["tool", "arg"],
                cwd=Path.cwd(),
                timeout_seconds=12,
                input_text="prompt",
                env={"SAFE": "1"},
            )

        self.assertEqual(result.returncode, 0)
        popen.assert_called_once_with(
            ["tool", "arg"],
            cwd=str(Path.cwd()),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={"SAFE": "1"},
            start_new_session=True,
        )
        process.communicate.assert_called_once_with(input="prompt", timeout=12)

    @unittest.skipUnless(os.name == "posix", "POSIX 프로세스 그룹 동작을 검증합니다")
    def test_subprocess_timeout_kills_the_whole_process_group(self) -> None:
        timeout = subprocess.TimeoutExpired(["tool"], 3, output=b"partial", stderr=b"late")
        process = mock.Mock()
        process.pid = 4321
        process.poll.return_value = None
        process.communicate.side_effect = [timeout, ("partial", "late")]
        with mock.patch.object(execute.subprocess, "Popen", return_value=process):
            with mock.patch.object(execute.os, "killpg") as killpg:
                with self.assertRaises(execute.CommandTimeout) as raised:
                    execute.CommandRunner().run(
                        ["tool"], cwd=Path.cwd(), timeout_seconds=3
                    )
        killpg.assert_called_once_with(4321, signal.SIGKILL)
        process.kill.assert_not_called()
        self.assertEqual(raised.exception.stdout, "partial")
        self.assertEqual(raised.exception.stderr, "late")

    @unittest.skipUnless(os.name == "posix", "POSIX 프로세스 그룹 동작을 검증합니다")
    def test_keyboard_interrupt_kills_the_whole_process_group(self) -> None:
        process = mock.Mock()
        process.pid = 9876
        process.poll.return_value = None
        process.communicate.side_effect = [KeyboardInterrupt(), ("", "")]
        with mock.patch.object(execute.subprocess, "Popen", return_value=process):
            with mock.patch.object(execute.os, "killpg") as killpg:
                with self.assertRaises(execute.CommandCancelled):
                    execute.CommandRunner().run(
                        ["tool"], cwd=Path.cwd(), timeout_seconds=3
                    )
        killpg.assert_called_once_with(9876, signal.SIGKILL)
        process.kill.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX 프로세스 그룹 동작을 검증합니다")
    def test_timeout_kills_group_and_bounds_cleanup_after_leader_exits(self) -> None:
        initial = subprocess.TimeoutExpired(
            ["tool"], 3, output=b"initial", stderr=b"initial-error"
        )
        first_cleanup = subprocess.TimeoutExpired(
            ["tool"], execute.PROCESS_CLEANUP_TIMEOUT_SECONDS, output=b"child-open"
        )
        second_cleanup = subprocess.TimeoutExpired(
            ["tool"], execute.PROCESS_CLEANUP_TIMEOUT_SECONDS, stderr=b"child-error"
        )
        process = mock.Mock()
        process.pid = 2468
        process.poll.return_value = 0
        process.communicate.side_effect = [initial, first_cleanup, second_cleanup]
        process.wait.side_effect = subprocess.TimeoutExpired(
            ["tool"], execute.PROCESS_CLEANUP_TIMEOUT_SECONDS
        )

        with mock.patch.object(execute.subprocess, "Popen", return_value=process):
            with mock.patch.object(execute.os, "killpg") as killpg:
                with self.assertRaises(execute.CommandTimeout) as raised:
                    execute.CommandRunner().run(
                        ["tool"], cwd=Path.cwd(), timeout_seconds=3
                    )

        self.assertEqual(3, killpg.call_count)
        self.assertTrue(
            all(call == mock.call(2468, signal.SIGKILL) for call in killpg.call_args_list)
        )
        process.kill.assert_not_called()
        self.assertEqual(
            [
                mock.call(input=None, timeout=3),
                mock.call(timeout=execute.PROCESS_CLEANUP_TIMEOUT_SECONDS),
                mock.call(timeout=execute.PROCESS_CLEANUP_TIMEOUT_SECONDS),
            ],
            process.communicate.call_args_list,
        )
        process.stdin.close.assert_called_once_with()
        process.stdout.close.assert_called_once_with()
        process.stderr.close.assert_called_once_with()
        process.wait.assert_called_once_with(
            timeout=execute.PROCESS_CLEANUP_TIMEOUT_SECONDS
        )
        self.assertEqual("child-open", raised.exception.stdout)
        self.assertEqual("child-error", raised.exception.stderr)

    def test_subprocess_launch_failure_is_an_infrastructure_blocker(self) -> None:
        with mock.patch.object(
            execute.subprocess,
            "Popen",
            side_effect=FileNotFoundError("없음"),
        ):
            with self.assertRaises(execute.InfrastructureBlocker):
                execute.CommandRunner().run(["tool"], cwd=Path.cwd(), timeout_seconds=3)


class CliStatusTest(unittest.TestCase):
    @staticmethod
    def write_ready_index(run_dir: Path) -> None:
        step_evidence = run_dir / "evidence" / "step-1" / "attempt-1"
        final_evidence = run_dir / "evidence" / "final" / "validation-1"
        step_evidence.mkdir(parents=True)
        final_evidence.mkdir(parents=True)
        (step_evidence / "result.json").write_text('{}\n', encoding="utf-8")
        (final_evidence / "result.json").write_text('{}\n', encoding="utf-8")
        execute.atomic_write_json(
            run_dir / "index.json",
            {
                "schema_version": 1,
                "status": "ready",
                "workspace_sha256": "f" * 64,
                "steps": [
                    {
                        "id": "step-1",
                        "status": "passed",
                        "attempts": [
                            {
                                "status": "passed",
                                "evidence": "evidence/step-1/attempt-1",
                                "evidence_sha256": execute.hash_evidence_directory(
                                    step_evidence
                                ),
                            }
                        ],
                    }
                ],
                "final_acceptance": {"status": "passed"},
                "final_verifier": {"verdict": "pass"},
                "final_validation": {
                    "status": "passed",
                    "evidence": "evidence/final/validation-1",
                    "evidence_sha256": execute.hash_evidence_directory(final_evidence),
                },
            },
        )

    def test_status_exit_code_reflects_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            self.write_ready_index(run_dir)
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = execute.main(["--status", "--run-dir", str(run_dir)])
        self.assertEqual(exit_code, 0)
        rendered = json.loads(output.getvalue())
        self.assertEqual(rendered["status"], "ready")
        self.assertEqual(rendered["scv_line"], "Job's finished.")

    @unittest.skipUnless(os.name == "posix", "POSIX 파일 잠금 동작을 검증합니다")
    def test_status_cli_returns_75_when_run_is_locked_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            self.write_ready_index(run_dir)
            before = (run_dir / "index.json").read_bytes()
            descriptor = os.open(
                run_dir / execute.RUN_LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600
            )
            execute.fcntl.flock(
                descriptor, execute.fcntl.LOCK_EX | execute.fcntl.LOCK_NB
            )
            stderr = io.StringIO()
            try:
                with mock.patch("sys.stderr", stderr):
                    exit_code = execute.main(
                        ["--status", "--run-dir", str(run_dir)]
                    )
            finally:
                execute.fcntl.flock(descriptor, execute.fcntl.LOCK_UN)
                os.close(descriptor)

            self.assertEqual(before, (run_dir / "index.json").read_bytes())

        self.assertEqual(execute.EXECUTION_BUSY_EXIT_CODE, exit_code)
        self.assertIn("사용 중", stderr.getvalue())

    def test_run_cli_forwards_the_direct_contract(self) -> None:
        outcome = execute.RunOutcome("ready", Path("/tmp/run/index.json"), 2, 2)
        with mock.patch.object(execute, "execute_plan", return_value=outcome) as execute_plan:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = execute.main(
                    [
                        "plan.json",
                        "--root",
                        "worktree",
                        "--run-dir",
                        "task/run",
                        "--expected-base",
                        "a" * 40,
                        "--timeout",
                        "90",
                    ]
                )
        self.assertEqual(exit_code, 0)
        execute_plan.assert_called_once_with(
            Path("plan.json"),
            root=Path("worktree"),
            run_dir=Path("task/run"),
            expected_base="a" * 40,
            timeout_seconds=90,
            codex_binary="codex",
            revalidate_ready=False,
            learning_root=None,
        )
        rendered = json.loads(output.getvalue())
        self.assertEqual(rendered["status"], "ready")
        self.assertEqual(rendered["scv_line"], "Job's finished.")

    def test_run_cli_returns_130_for_cancellation(self) -> None:
        with mock.patch.object(
            execute, "execute_plan", side_effect=execute.CommandCancelled("stop")
        ):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                exit_code = execute.main(
                    ["plan.json", "--root", "worktree", "--run-dir", "task/run"]
                )
        self.assertEqual(exit_code, 130)


if __name__ == "__main__":
    unittest.main()
