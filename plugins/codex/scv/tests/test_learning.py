from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import learning  # noqa: E402
import runtime  # noqa: E402


def _record_observation_process(
    root: str,
    task_number: int,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    try:
        store = learning.LearningStore(Path(root))
        failure = learning.build_failure_record(
            task_id=f"task-{task_number}",
            plan_sha256=(str(task_number) * 64)[:64],
            step_id="step-1",
            attempt_number=1,
            stage="acceptance",
            message=f"failure-{task_number}",
            evidence_sha256=(hex(task_number)[2:] * 64)[:64],
        )
        analysis = {
            "classification": "implementation",
            "diagnosis": f"diagnosis-{task_number}",
            "failed_approaches": [],
            "next_actions": ["retry"],
            "verification_checks": ["verify"],
            "candidate_lesson": f"lesson-{task_number}",
        }
        start.wait(10)
        observation = store.record_observation(
            failure,
            analysis,
            analyst_evidence_sha256=("a" if task_number == 1 else "b") * 64,
        )
        results.put(("ok", observation["observation_id"]))
    except Exception as exc:  # pragma: no cover - asserted by the parent.
        results.put(("error", repr(exc)))


class FailureNormalizationTests(unittest.TestCase):
    def test_redaction_removes_common_secret_forms(self) -> None:
        raw = (
            "Authorization: Bearer example-secret-value\n"
            "OPENAI_API_KEY=example-secret-value\n"
            "DB_PASSWORD: example-secret-value\n"
            "token=example-secret-value\n"
            '{"token":"json-secret-value"}\n'
            "sk-exampleSecret123456\n"
            "ghp_abcdefghijklmnopqrstuvwxyz123456\n"
            "sessionid=browser-session-secret\n"
            "Authorization: Basic dXNlcjpzZWNyZXQ=\n"
            "postgres://user:database-password@example.invalid/db\n"
            "eyJabcdefghijk.abcdefghijkl.abcdefghijkl\n"
            "-----BEGIN PRIVATE KEY-----\nprivate-material\n"
            "-----END PRIVATE KEY-----\n"
        )

        redacted = learning.redact_text(raw)

        self.assertNotIn("example-secret-value", redacted)
        self.assertNotIn("exampleSecret123456", redacted)
        self.assertNotIn("json-secret-value", redacted)
        self.assertNotIn("browser-session-secret", redacted)
        self.assertNotIn("database-password", redacted)
        self.assertNotIn("private-material", redacted)
        self.assertNotIn("ghp_", redacted)
        self.assertNotIn("dXNlcjpzZWNyZXQ", redacted)
        self.assertNotIn("eyJabcdefghijk", redacted)
        self.assertIn("<redacted>", redacted)

    def test_signature_normalizes_volatile_values(self) -> None:
        first = learning.failure_signature(
            stage="acceptance",
            command="python -m unittest",
            exit_code=1,
            message=(
                "2026-07-13T10:11:12Z pid=123 "
                "/private/var/folders/aa/tmp/file.py:41 assertion failed "
                "123e4567-e89b-12d3-a456-426614174000"
            ),
        )
        second = learning.failure_signature(
            stage="acceptance",
            command="python   -m   unittest",
            exit_code=1,
            message=(
                "2027-08-14T20:21:22Z pid=999 "
                "/private/var/folders/bb/tmp/other.py:88 assertion failed "
                "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            ),
        )

        self.assertEqual(first[0], second[0])

    def test_signature_distinguishes_material_failure_fields(self) -> None:
        base = dict(
            stage="acceptance",
            command="python -m unittest",
            exit_code=1,
            message="assertion failed",
        )
        signature = learning.failure_signature(**base)[0]

        for changed in (
            {**base, "stage": "verifier"},
            {**base, "command": "python -m pytest"},
            {**base, "exit_code": 2},
            {**base, "message": "module import failed"},
            {**base, "scope": "different approved step"},
        ):
            with self.subTest(changed=changed):
                self.assertNotEqual(signature, learning.failure_signature(**changed)[0])

    def test_signature_does_not_contain_secret_material(self) -> None:
        signature, normalized, _ = learning.failure_signature(
            stage="acceptance",
            command="check",
            exit_code=1,
            message="OPENAI_API_KEY=very-secret-value failed",
        )
        self.assertNotIn("very-secret-value", normalized)
        self.assertRegex(signature, r"^[0-9a-f]{64}$")


class LearningStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "learning"
        self.store = learning.LearningStore(self.root)
        self.failure = learning.build_failure_record(
            task_id="task-1",
            plan_sha256="a" * 64,
            step_id="step-1",
            attempt_number=1,
            stage="acceptance",
            message="test failed",
            evidence_sha256="b" * 64,
            acceptance_records=[
                {
                    "command": "python -m unittest",
                    "status": "failed",
                    "returncode": 1,
                    "stderr": "assertion failed",
                }
            ],
            context_sha256="c" * 64,
        )
        self.analysis = {
            "classification": "implementation",
            "diagnosis": "경계 조건 구현이 없습니다.",
            "failed_approaches": ["성공 경로만 수정함"],
            "next_actions": ["실패 테스트를 먼저 재현함"],
            "verification_checks": ["같은 인수 명령을 실행함"],
            "candidate_lesson": "경계 조건 테스트를 먼저 재현합니다.",
        }

    def observation(self) -> dict:
        return self.store.record_observation(
            self.failure,
            self.analysis,
            analyst_evidence_sha256="d" * 64,
        )

    def candidate(self) -> dict:
        observation = self.observation()
        return self.store.create_candidate(
            observation["observation_id"], successful_evidence_sha256="e" * 64
        )

    def test_candidate_is_not_retrieved_until_evidence_backed_approval(self) -> None:
        candidate = self.candidate()

        self.assertEqual([], self.store.active_lessons(self.failure["signature"]))
        with self.assertRaises(learning.LearningError):
            self.store.approve(candidate["lesson_id"], approval_evidence={})

        active = self.store.approve(
            candidate["lesson_id"],
            approval_evidence={
                "execution_index_sha256": "f" * 64,
                "final_evidence_sha256": "1" * 64,
            },
        )

        self.assertEqual("active", active["status"])
        self.assertEqual(
            [candidate["lesson_id"]],
            [item["lesson_id"] for item in self.store.active_lessons(self.failure["signature"])],
        )

    def test_active_lesson_becomes_suspect_and_is_not_retrieved(self) -> None:
        candidate = self.candidate()
        self.store.approve(
            candidate["lesson_id"],
            approval_evidence={
                "execution_index_sha256": "f" * 64,
                "final_evidence_sha256": "1" * 64,
            },
        )

        changed = self.store.mark_suspect([candidate["lesson_id"]])

        self.assertEqual("suspect", changed[0]["status"])
        self.assertEqual([], self.store.active_lessons(self.failure["signature"]))
        with self.assertRaises(learning.LearningError):
            self.store.approve(
                candidate["lesson_id"],
                approval_evidence={
                    "execution_index_sha256": "2" * 64,
                    "final_evidence_sha256": "3" * 64,
                },
            )

    def test_tampered_observation_and_lesson_are_rejected(self) -> None:
        candidate = self.candidate()
        observation_path = (
            self.root / "observations" / f"{candidate['source_observation_id']}.json"
        )
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        observation["analysis"]["diagnosis"] = "변조됨"
        observation_path.write_text(json.dumps(observation), encoding="utf-8")
        with self.assertRaises(learning.LearningError):
            self.store.load_observation(candidate["source_observation_id"])

        lesson_path = self.root / "lessons" / f"{candidate['lesson_id']}.json"
        lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
        lesson["guidance"] = "변조됨"
        lesson_path.write_text(json.dumps(lesson), encoding="utf-8")
        with self.assertRaises(learning.LearningError):
            self.store.load_lesson(candidate["lesson_id"])

    def test_corrupt_candidate_does_not_poison_active_lookup(self) -> None:
        candidate = self.candidate()
        lesson_path = self.root / "lessons" / f"{candidate['lesson_id']}.json"
        lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
        lesson["guidance"] = "변조됨"
        lesson_path.write_text(json.dumps(lesson), encoding="utf-8")

        self.assertEqual([], self.store.active_lessons(self.failure["signature"]))
        with self.assertRaises(learning.LearningError):
            self.store.list_lessons()

    def test_symlinked_learning_directory_is_rejected(self) -> None:
        alternate = Path(self.temporary.name) / "alternate"
        alternate.mkdir()
        self.root.mkdir()
        os.symlink(alternate, self.root / "lessons")

        with self.assertRaises(learning.LearningError):
            self.store.list_lessons()

    def test_symlinked_learning_root_is_rejected_before_use(self) -> None:
        alternate = Path(self.temporary.name) / "alternate-root"
        alternate.mkdir()
        linked_root = Path(self.temporary.name) / "linked-learning"
        os.symlink(alternate, linked_root)

        with self.assertRaises(learning.LearningError):
            learning.LearningStore(linked_root)

    def test_symlinked_parent_is_rejected_when_loading_observation(self) -> None:
        observation = self.observation()
        original = self.root / "observations"
        alternate = Path(self.temporary.name) / "alternate-observations"
        original.rename(alternate)
        os.symlink(alternate, original)

        with self.assertRaises(learning.LearningError):
            self.store.load_observation(observation["observation_id"])

    def test_proposal_filename_and_content_id_must_match(self) -> None:
        controller_analysis = dict(self.analysis)
        controller_analysis["classification"] = "controller"
        observation = self.store.record_observation(
            self.failure,
            controller_analysis,
            analyst_evidence_sha256="d" * 64,
        )
        proposal = self.store.create_proposal(
            observation["observation_id"], kind="retry_exhausted"
        )
        original = self.root / "proposals" / f"{proposal['proposal_id']}.json"
        mismatched = self.root / "proposals" / f"{'f' * 64}.json"
        original.rename(mismatched)

        with self.assertRaises(learning.LearningError):
            self.store.list_proposals()

    def test_controller_proposal_handoff_and_close_are_auditable(self) -> None:
        controller_analysis = dict(self.analysis)
        controller_analysis["classification"] = "controller"
        observation = self.store.record_observation(
            self.failure,
            controller_analysis,
            analyst_evidence_sha256="d" * 64,
        )
        proposal = self.store.create_proposal(
            observation["observation_id"], kind="controller-defect"
        )

        handed_off = self.store.handoff_proposal(
            proposal["proposal_id"],
            repair_repo="/tmp/scv-source",
            repair_plugin_root="/tmp/scv-source/plugins/codex/scv",
            repair_task_id="repair-task",
        )

        self.assertEqual("handed-off", handed_off["status"])
        self.assertEqual(
            "/tmp/scv-source/plugins/codex/scv",
            handed_off["handoff"]["repair_plugin_root"],
        )
        self.assertEqual(proposal["proposal_id"], handed_off["proposal_id"])
        self.assertEqual(
            [], self.store.list_proposals(statuses=("proposed",))
        )
        closed = self.store.close_proposal(
            proposal["proposal_id"], reason="수리 태스크로 인계 완료"
        )
        self.assertEqual("closed", closed["status"])
        with self.assertRaises(learning.LearningError):
            self.store.close_proposal(
                proposal["proposal_id"], reason="중복 종료"
            )

    def test_non_macos_store_is_rejected_before_directory_creation(self) -> None:
        unsupported = Path(self.temporary.name) / "unsupported"
        with mock.patch.object(runtime.sys, "platform", "linux"):
            with self.assertRaises(learning.LearningError):
                learning.LearningStore(unsupported)
        self.assertFalse(unsupported.exists())

    def test_concurrent_observations_are_atomically_preserved(self) -> None:
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(
                target=_record_observation_process,
                args=(str(self.root), number, start, results),
            )
            for number in (1, 2)
        ]
        for process in processes:
            process.start()
        start.set()
        received = [results.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=15)
            self.assertEqual(0, process.exitcode)

        self.assertTrue(all(status == "ok" for status, _ in received), received)
        observation_ids = {identifier for _, identifier in received}
        self.assertEqual(2, len(observation_ids))
        self.assertEqual(
            observation_ids,
            {
                self.store.load_observation(identifier)["observation_id"]
                for identifier in observation_ids
            },
        )


if __name__ == "__main__":
    unittest.main()
