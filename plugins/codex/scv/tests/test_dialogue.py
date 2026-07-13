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

    def test_output_decoration_is_computed_without_mutating_state(self) -> None:
        payload = {"task_id": "dialogue", "state": State.READY.value}

        rendered = scv_dialogue.decorate_scv_output(payload)

        self.assertNotIn("scv_line", payload)
        self.assertEqual("Job's finished.", rendered["scv_line"])
        self.assertEqual(
            "I can't build there.",
            scv_dialogue.decorate_scv_output({"status": "blocked"})["scv_line"],
        )

    def test_control_plane_emit_includes_the_current_line(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            scv.emit({"state": State.EXECUTING.value})

        self.assertEqual("Orders received.", json.loads(output.getvalue())["scv_line"])


if __name__ == "__main__":
    unittest.main()
