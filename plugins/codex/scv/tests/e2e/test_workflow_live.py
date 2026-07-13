from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCV_SCRIPT = PLUGIN_ROOT / "scripts" / "scv.py"
LIVE_E2E_ENABLED = os.environ.get("SCV_LIVE_E2E") == "1"


@unittest.skipUnless(
    LIVE_E2E_ENABLED,
    "SCV_LIVE_E2E=1일 때 실제 Codex Live E2E를 실행합니다",
)
class SCVLiveWorkflowE2ETests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="scv-live-e2e-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "fixture-repo"
        self.repo.mkdir()
        self.state_root = self.root / "state"
        self.task_id = "live-calculator-e2e"
        self._git("init", "-b", "main")
        self._git("config", "user.name", "SCV Live E2E")
        self._git("config", "user.email", "scv-live@example.test")
        (self.repo / "calculator.py").write_text(
            "def add(left: int, right: int) -> int:\n"
            "    return left + right\n",
            encoding="utf-8",
        )
        (self.repo / "test_calculator.py").write_text(
            "import unittest\n\n"
            "from calculator import add\n\n\n"
            "class CalculatorTests(unittest.TestCase):\n"
            "    def test_add(self) -> None:\n"
            "        self.assertEqual(5, add(2, 3))\n\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        (self.repo / ".gitignore").write_text(
            ".scv-live-retry\n", encoding="utf-8"
        )
        self._git("add", ".gitignore", "calculator.py", "test_calculator.py")
        self._git("commit", "-m", "chore: initialize calculator fixture")

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repo), *arguments],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.stdout.strip()

    @staticmethod
    def _decode_json_stream(output: str) -> list[object]:
        decoder = json.JSONDecoder()
        values: list[object] = []
        position = 0
        while position < len(output):
            while position < len(output) and output[position].isspace():
                position += 1
            if position >= len(output):
                break
            value, position = decoder.raw_decode(output, position)
            values.append(value)
        return values

    def _scv_command(self, *arguments: str) -> list[str]:
        return [
            sys.executable,
            str(SCV_SCRIPT),
            "--repo",
            str(self.repo),
            "--state-root",
            str(self.state_root),
            *arguments,
        ]

    def _payload_from_result(
        self,
        result: subprocess.CompletedProcess[str],
        command: list[str],
    ) -> dict[str, object]:
        self.assertEqual(
            0,
            result.returncode,
            "SCV command failed:\n"
            f"command={command!r}\n"
            f"stdout={result.stdout}\n"
            f"stderr={result.stderr}",
        )
        values = self._decode_json_stream(result.stdout)
        self.assertTrue(values, f"SCV command returned no JSON: {result.stdout!r}")
        payload = values[-1]
        self.assertIsInstance(payload, dict)
        return payload

    def _scv(self, *arguments: str, timeout: int = 900) -> dict[str, object]:
        command = self._scv_command(*arguments)
        result = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return self._payload_from_result(result, command)

    def _execute_with_progress(
        self, task_id: str, *, timeout: int = 900
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        command = self._scv_command("execute", task_id, "--timeout", "300")
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + timeout
        observed: list[dict[str, object]] = []
        last_key: tuple[object, ...] | None = None
        while process.poll() is None:
            self.assertLess(time.monotonic(), deadline, "SCV execute timed out")
            status = self._scv("status", task_id, timeout=60)
            progress = status.get("execution_progress")
            if isinstance(progress, dict):
                key = (
                    progress.get("status"),
                    progress.get("stage"),
                    progress.get("attempt"),
                    progress.get("completed_steps"),
                )
                if key != last_key:
                    observed.append(dict(progress))
                    last_key = key
            time.sleep(1)
        stdout, stderr = process.communicate(timeout=30)
        result = subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout,
            stderr,
        )
        return self._payload_from_result(result, command), observed

    def test_full_workflow_reaches_verified_ready_with_real_codex(self) -> None:
        started = self._scv(
            "start",
            "full",
            "--task-id",
            self.task_id,
            "--request",
            "Add a typed subtract function and its unittest without changing add.",
        )
        self.assertEqual("INTAKING", started["state"])

        specification = self.root / "spec.md"
        specification.write_text(
            "# Specification\n\n"
            "Add `subtract(left: int, right: int) -> int` to calculator.py.\n"
            "Keep `add` unchanged and add a unittest for subtraction.\n"
            "All calculator unittests and `git diff --check` must pass.\n",
            encoding="utf-8",
        )
        awaiting_spec = self._scv(
            "submit-spec", self.task_id, "--spec", str(specification)
        )
        self.assertEqual("AWAITING_SPEC_APPROVAL", awaiting_spec["state"])
        planning = self._scv("approve-spec", self.task_id)
        self.assertEqual("PLANNING", planning["state"])

        plan = self.root / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": self.task_id,
                    "task": "Add typed subtraction with regression coverage",
                    "steps": [
                        {
                            "id": "step-1",
                            "title": "Implement and test subtraction",
                            "instructions": (
                                "Add `subtract(left: int, right: int) -> int` to "
                                "calculator.py without changing `add`. Add a unittest "
                                "covering a representative subtraction result."
                            ),
                            "acceptance": [
                                "python3 -m unittest discover -s . -p 'test_*.py'"
                            ],
                            "timeout_seconds": 300,
                        }
                    ],
                    "final_acceptance": ["git diff --check"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        awaiting_plan = self._scv(
            "submit-plan", self.task_id, "--plan", str(plan)
        )
        self.assertEqual("AWAITING_PLAN_APPROVAL", awaiting_plan["state"])
        revalidation = self._scv("approve-plan", self.task_id)
        self.assertEqual("BASE_REVALIDATION", revalidation["state"])

        executing = self._scv("materialize", self.task_id)
        self.assertEqual("EXECUTING", executing["state"])
        worktree = Path(str(executing["worktree"]["path"]))
        self.assertTrue(worktree.is_dir())

        handoff, observed = self._execute_with_progress(self.task_id)
        self.assertEqual("HANDOFF", handoff["state"])
        self.assertTrue(
            any(progress.get("stage") == "worker" for progress in observed),
            observed,
        )
        ready = self._scv("handoff", self.task_id)
        self.assertEqual("READY", ready["state"])
        status = self._scv("status", self.task_id)
        self.assertEqual("READY", status["state"])
        self.assertEqual("verified", status["execution_integrity"]["status"])
        self.assertEqual("Job's finished.", status["scv_line"])

        calculator = (worktree / "calculator.py").read_text(encoding="utf-8")
        self.assertIn("def add", calculator)
        self.assertIn("def subtract", calculator)
        self.assertNotIn(
            "def subtract",
            (self.repo / "calculator.py").read_text(encoding="utf-8"),
        )
        test_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(worktree.glob("test*.py"))
        )
        self.assertIn("subtract", test_sources)
        self.assertTrue(worktree.is_dir(), "READY must preserve the worktree")

    def test_failure_analyst_retries_after_a_real_acceptance_failure(self) -> None:
        self.task_id = "live-retry-e2e"
        self._scv(
            "start",
            "full",
            "--task-id",
            self.task_id,
            "--request",
            "Add typed subtraction and exercise SCV's bounded retry path.",
        )
        specification = self.root / "retry-spec.md"
        specification.write_text(
            "# Specification\n\n"
            "Add `subtract(left: int, right: int) -> int` and a unittest.\n"
            "The Live E2E controller acceptance probe must fail exactly once, then "
            "the retry and all calculator tests must pass.\n",
            encoding="utf-8",
        )
        self._scv("submit-spec", self.task_id, "--spec", str(specification))
        self._scv("approve-spec", self.task_id)

        retry_probe = (
            "python3 -c \"from pathlib import Path; "
            "p=Path('.scv-live-retry'); seen=p.exists(); "
            "p.write_text('seen\\\\n', encoding='utf-8'); "
            "raise SystemExit(0 if seen else 1)\""
        )
        plan = self.root / "retry-plan.json"
        plan.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": self.task_id,
                    "task": "Implement subtraction and verify failure-aware retry",
                    "steps": [
                        {
                            "id": "step-1",
                            "title": "Implement subtraction with a one-time retry probe",
                            "instructions": (
                                "Add the typed subtract function and its unittest. "
                                "Do not run the `.scv-live-retry` acceptance probe and "
                                "do not create or modify that ignored marker; it is owned "
                                "by the controller specifically to exercise one retry."
                            ),
                            "acceptance": [
                                retry_probe,
                                "python3 -m unittest discover -s . -p 'test_*.py'",
                            ],
                            "timeout_seconds": 300,
                        }
                    ],
                    "final_acceptance": ["git diff --check"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self._scv("submit-plan", self.task_id, "--plan", str(plan))
        self._scv("approve-plan", self.task_id)
        executing = self._scv("materialize", self.task_id)
        worktree = Path(str(executing["worktree"]["path"]))

        handoff, observed = self._execute_with_progress(self.task_id)

        self.assertEqual("HANDOFF", handoff["state"])
        stage_attempts = {
            (progress.get("stage"), progress.get("attempt"))
            for progress in observed
        }
        self.assertIn(("failure-analysis", 1), stage_attempts)
        self.assertIn(("worker", 2), stage_attempts)
        status = self._scv("status", self.task_id)
        task_dir = Path(str(status["task_dir"]))
        execution = status["artifacts"]["execution"]
        index_path = task_dir / str(execution["path"])
        index = json.loads(index_path.read_text(encoding="utf-8"))
        attempts = index["steps"][0]["attempts"]
        self.assertEqual(["failed", "passed"], [item["status"] for item in attempts])
        self.assertEqual("acceptance", attempts[0]["failure"]["stage"])
        self.assertEqual("analyzed", attempts[0]["learning"]["status"])
        analysis_dir = task_dir / "runs" / index["plan_sha256"] / attempts[0][
            "learning"
        ]["analysis_evidence"]
        self.assertTrue((analysis_dir / "failure-analyst-final.json").is_file())
        self.assertTrue((worktree / ".scv-live-retry").is_file())

        ready = self._scv("handoff", self.task_id)
        self.assertEqual("READY", ready["state"])


if __name__ == "__main__":
    unittest.main()
