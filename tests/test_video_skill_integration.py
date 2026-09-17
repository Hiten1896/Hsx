import unittest
from pathlib import Path

from agent.skill_loader import load_skills

ROOT = Path(__file__).resolve().parent.parent


class VideoSkillIntegrationTests(unittest.TestCase):
    def test_video_edit_skill_is_auto_discovered(self):
        tools, skill_map = load_skills(str(ROOT / "skills"))
        names = {tool["name"] for tool in tools}

        self.assertIn("video_edit", names)
        self.assertTrue(callable(skill_map["video_edit"]))

    def test_video_tooling_status_is_reported(self):
        from video_tools.pipeline import inspect_video_tooling

        status = inspect_video_tooling()

        self.assertIn("ffmpeg", status)
        self.assertIn("moviepy", status)
        self.assertIn("whisper", status)
