from __future__ import annotations

import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from plugins.codex.scv.scripts import scv, execute, runtime


class RuntimeRequirementTests(unittest.TestCase):
    def test_non_macos_is_rejected_in_korean(self) -> None:
        with self.assertRaisesRegex(runtime.RuntimeRequirementError, "macOS 전용"):
            runtime.require_macos("linux")

    def test_codex_version_and_required_flags_are_enforced(self) -> None:
        runtime.validate_codex_capabilities(
            "codex-cli 0.144.1",
            "--config --cd --color --ephemeral --ignore-user-config --json "
            "--output-schema --output-last-message --sandbox",
            "-P --sandbox-state-disable-network -C",
        )
        with self.assertRaisesRegex(runtime.RuntimeRequirementError, "0.144.1"):
            runtime.validate_codex_capabilities(
                "codex-cli 0.143.0",
                "--config --cd --color --ephemeral --ignore-user-config --json "
                "--output-schema --output-last-message --sandbox",
                "-P --sandbox-state-disable-network -C",
            )
        with self.assertRaisesRegex(runtime.RuntimeRequirementError, "--ephemeral"):
            runtime.validate_codex_capabilities(
                "codex-cli 0.144.1",
                "--config --cd --color --ignore-user-config --json "
                "--output-schema --output-last-message --sandbox",
                "-P --sandbox-state-disable-network -C",
            )

    def test_nested_seatbelt_failure_has_korean_host_approval_guidance(self) -> None:
        detail = execute._sandbox_failure_detail(
            "sandbox-exec: sandbox_apply: Operation not permitted"
        )

        self.assertIn("호스트 승인 실행", detail)

    def test_status_rejects_non_macos_before_lock_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            with mock.patch.object(runtime.sys, "platform", "linux"):
                with self.assertRaisesRegex(execute.InfrastructureBlocker, "macOS 전용"):
                    execute.read_status(run_dir)
            self.assertFalse(run_dir.exists())

    def test_execute_rejects_non_macos_before_run_state_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            plan = base / "plan.json"
            run_dir = base / "run"
            root = base / "worktree"
            root.mkdir()
            plan.write_text("{}", encoding="utf-8")
            with mock.patch.object(runtime.sys, "platform", "linux"):
                with self.assertRaises(execute.InfrastructureBlocker):
                    execute.execute_plan(plan, root=root, run_dir=run_dir)
            self.assertFalse(run_dir.exists())

    def test_scv_cli_rejects_non_macos_before_state_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_root = Path(temporary) / "state"
            stderr = io.StringIO()
            with mock.patch.object(runtime.sys, "platform", "linux"), mock.patch(
                "sys.stderr", stderr
            ):
                exit_code = scv.main(
                    [
                        "--repo",
                        temporary,
                        "--state-root",
                        str(state_root),
                        "start",
                        "analyze",
                        "--task-id",
                        "unsupported",
                        "--request",
                        "요청",
                    ]
                )
            self.assertEqual(2, exit_code)
            self.assertIn("macOS 전용", stderr.getvalue())
            self.assertFalse(state_root.exists())

    def test_full_start_preflight_fails_before_state_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "init", "-q", str(repo)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            state_root = Path(temporary) / "state"
            stderr = io.StringIO()
            with mock.patch.object(
                scv,
                "preflight_start_runtime",
                side_effect=execute.InfrastructureBlocker("Codex 사전 점검 실패"),
            ), mock.patch("sys.stderr", stderr):
                exit_code = scv.main(
                    [
                        "--repo",
                        str(repo),
                        "--state-root",
                        str(state_root),
                        "start",
                        "full",
                        "--task-id",
                        "preflight-failure",
                        "--request",
                        "요청",
                    ]
                )
            self.assertEqual(2, exit_code)
            self.assertIn("Codex 사전 점검 실패", stderr.getvalue())
            self.assertFalse(state_root.exists())


if __name__ == "__main__":
    unittest.main()
