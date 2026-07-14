from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import scv  # noqa: E402
import scv_dialogue  # noqa: E402
from scv_state import State  # noqa: E402


class SCVDialogueTests(unittest.TestCase):
    def test_every_lifecycle_state_has_an_scv_line(self) -> None:
        expected = {
            State.NEW: "Reportin' for duty.",
            State.INTAKING: "I read you.",
            State.AWAITING_SPEC_APPROVAL: "Orders, Cap'n?",
            State.PLANNING: "SCV good to go, sir.",
            State.AWAITING_PLAN_APPROVAL: "Yes sir?",
            State.BASE_REVALIDATION: "Affirmative.",
            State.MATERIALIZING_WORKTREE: "Right away sir.",
            State.EXECUTING: "Orders received.",
            State.HANDOFF: "Roger that.",
            State.READY: "Job's finished.",
            State.BLOCKED: "I can't build there.",
            State.ABANDONED: "I'm not readin' you clearly.",
        }

        self.assertEqual(
            {state.value: line for state, line in expected.items()},
            scv_dialogue.SCV_STATE_LINES,
        )

    def test_every_lifecycle_state_has_a_korean_label(self) -> None:
        expected = {
            State.NEW: "태스크 초기화",
            State.INTAKING: "요구사항 접수",
            State.AWAITING_SPEC_APPROVAL: "스펙 승인 대기",
            State.PLANNING: "구현 계획 작성",
            State.AWAITING_PLAN_APPROVAL: "계획 승인 대기",
            State.BASE_REVALIDATION: "승인 기준 재확인",
            State.MATERIALIZING_WORKTREE: "격리 워크트리 생성",
            State.EXECUTING: "실행 중",
            State.HANDOFF: "검증 결과 인계",
            State.READY: "인계 준비 완료",
            State.BLOCKED: "차단됨",
            State.ABANDONED: "포기됨",
        }

        self.assertEqual(
            {state.value: label for state, label in expected.items()},
            scv_dialogue.SCV_STATE_LABELS,
        )

    def test_execution_progress_has_an_scv_line(self) -> None:
        expected = {
            "pending": "Reportin' for duty.",
            "running": "Orders received.",
            "passed": "Job's finished.",
            "ready": "Job's finished.",
            "blocked": "I can't build there.",
            "failed": "I can't build there.",
            "timed_out": "I can't build there.",
            "unavailable": "I can't build there.",
        }

        self.assertEqual(expected, scv_dialogue.SCV_PROGRESS_LINES)

    def test_execution_stages_use_actual_scv_lines(self) -> None:
        expected = {
            "starting": "Reportin' for duty.",
            "worker": "Orders received.",
            "acceptance": "Affirmative.",
            "verifier": "I read you.",
            "failure-analysis": "Come again, Cap'n?",
            "retry": "SCV good to go, sir.",
            "step-complete": "Job's finished.",
            "final-acceptance": "Affirmative.",
            "final-verifier": "I read you.",
            "complete": "Job's finished.",
            "blocked": "I can't build there.",
            "failed": "I can't build there.",
            "cancelled": "I'm not readin' you clearly.",
        }

        self.assertEqual(expected, scv_dialogue.SCV_STAGE_LINES)
        self.assertEqual(
            "Come again, Cap'n?",
            scv_dialogue.decorate_scv_output(
                {"status": "running", "stage": "failure-analysis"}
            )["scv_line"],
        )

    def test_execution_stages_have_korean_labels(self) -> None:
        expected = {
            "starting": "실행 환경 준비",
            "worker": "단계 구현",
            "acceptance": "단계 인수 검사",
            "verifier": "단계 읽기 전용 검증",
            "failure-analysis": "실패 증거 분석",
            "retry": "재시도 준비",
            "step-complete": "단계 완료",
            "final-acceptance": "전체 인수 검사",
            "final-verifier": "전체 읽기 전용 검증",
            "complete": "실행 완료",
            "blocked": "차단됨",
            "failed": "실패",
            "cancelled": "취소됨",
        }

        self.assertEqual(expected, scv_dialogue.SCV_STAGE_LABELS)

    def test_output_decoration_is_computed_without_mutating_state(self) -> None:
        payload = {"task_id": "dialogue", "state": State.READY.value}

        rendered = scv_dialogue.decorate_scv_output(payload)

        self.assertNotIn("scv_line", payload)
        self.assertNotIn("state_label", payload)
        self.assertEqual("Job's finished.", rendered["scv_line"])
        self.assertEqual("인계 준비 완료", rendered["state_label"])
        self.assertEqual(
            "I can't build there.",
            scv_dialogue.decorate_scv_output({"status": "blocked"})["scv_line"],
        )

        progress = {"status": "running", "stage": "worker"}
        rendered_progress = scv_dialogue.decorate_scv_output(progress)
        self.assertNotIn("stage_label", progress)
        self.assertEqual("단계 구현", rendered_progress["stage_label"])

    def test_control_plane_emit_includes_the_current_line(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            scv.emit({"state": State.EXECUTING.value})

        self.assertIn('"state_label": "실행 중"', output.getvalue())
        rendered = json.loads(output.getvalue())
        self.assertEqual(State.EXECUTING.value, rendered["state"])
        self.assertEqual("실행 중", rendered["state_label"])
        self.assertEqual("Orders received.", rendered["scv_line"])


if __name__ == "__main__":
    unittest.main()
