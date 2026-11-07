import unittest
from unittest.mock import MagicMock, patch

from agent.ml.planner import is_complex_task
from agent.swarm.roles import write_proposal_with_repair
import server


class TestPlanRoutingAndRepair(unittest.TestCase):
    def test_repro_prompt_never_considered_complex_task(self):
        # Repro from prompt:
        prompt = (
            "always remember that if I enter any username or password or mobile no. "
            "never store it, clear history after use, never send it to the LLM provider"
        )
        self.assertFalse(is_complex_task(prompt))

    def test_conversation_and_questions_never_complex_task(self):
        self.assertFalse(is_complex_task("hi, how are you today? What is the weather like?"))
        self.assertFalse(is_complex_task("what is the capital of France and explain why"))
        self.assertFalse(is_complex_task("turn on the camera and show me the room"))

    def test_multi_step_file_jobs_are_complex(self):
        doc_task = (
            "Create a document with an executive summary, then make a PDF version, "
            "and finally archive the files in a zip."
        )
        self.assertTrue(is_complex_task(doc_task))

    def test_write_proposal_repair_loop_recovers(self):
        # First attempt returns rejected code with exec(), second returns valid code
        attempts = []

        def fake_coder(prompt: str) -> str:
            attempts.append(prompt)
            if len(attempts) == 1:
                return "```python\nexec('bad_code')\n```"
            return "```python\ndef run_task():\n    return 'hello'\n```"

        def fake_validator(prompt: str) -> str:
            return '{"approved": true, "tests_reviewed": true, "rationale": "safe"}'

        result = write_proposal_with_repair(
            "Overall goal",
            "Task step",
            fake_coder,
            fake_validator,
            max_retries=4,
        )
        self.assertIn("run_task", result)
        self.assertEqual(len(attempts), 2)
        # Check that attempt 2 prompt included the error reason
        self.assertIn("rejected with error", attempts[1])

    def test_write_proposal_repair_loop_max_retries_exceeded(self):
        def bad_coder(prompt: str) -> str:
            return "```python\nexec('bad_code')\n```"

        def fake_validator(prompt: str) -> str:
            return '{"approved": true, "tests_reviewed": true, "rationale": "safe"}'

        with self.assertRaises(ValueError) as ctx:
            write_proposal_with_repair(
                "Overall goal",
                "Task step",
                bad_coder,
                fake_validator,
                max_retries=3,
            )
        self.assertIn("rejected after 3 attempt(s)", str(ctx.exception))

    def test_failure_deduplication_by_run_id(self):
        run_id = "test-run-dedupe-123"
        server._FAILED_RUN_IDS.discard(run_id)

        job = {"id": run_id, "session_id": "session-1", "state": "running"}
        mock_jobs = MagicMock()
        mock_jobs.fail.return_value = {"id": run_id, "state": "failed"}

        with patch.object(server, "ASK_JOBS", mock_jobs):
            server._fail_job(run_id, job, RuntimeError("First failure"))
            server._fail_job(run_id, job, RuntimeError("Second failure"))

        # Should only have called fail once due to deduplication
        self.assertEqual(mock_jobs.fail.call_count, 1)


if __name__ == "__main__":
    unittest.main()
