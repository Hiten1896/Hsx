import unittest

from video_tools.pipeline import (
    build_edit_plan,
    create_video_job,
    get_video_job,
    validate_edit_plan,
)


class VideoJobAndEdlTests(unittest.TestCase):
    def test_build_edit_plan_requires_project_metadata(self):
        plan = build_edit_plan(
            project_id="proj_001",
            prompt="Cut silence and keep the strongest three cuts.",
            source_files=["clip1.mp4", "clip2.mp4"],
            output_filename="final_cut.mp4",
        )

        self.assertEqual(plan["project_id"], "proj_001")
        self.assertIn("timeline", plan)
        self.assertIn("render", plan)
        self.assertIn("style", plan)

    def test_create_video_job_tracks_status(self):
        job = create_video_job(
            project_id="proj_002",
            prompt="Create a condensed highlight reel.",
            source_files=["a.mp4", "b.mp4"],
        )

        self.assertIn("job_id", job)
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["project_id"], "proj_002")

        stored = get_video_job(job["job_id"])
        self.assertEqual(stored["job_id"], job["job_id"])
        self.assertTrue(validate_edit_plan(stored["edit_plan"]))
