from pathlib import Path

from agent.jobs import JobQueue


def test_job_checkpoint_and_resume(tmp_path: Path):
    queue = JobQueue(tmp_path / "jobs.db")
    job = queue.create([{"name": "one"}, {"name": "two"}])
    seen = []

    def executor(step):
        seen.append(step["name"])
        if step["name"] == "one":
            return {"ok": True}
        return {"complete": True}

    queue.run(job["id"], executor)
    assert queue.get(job["id"])["state"] == "done"
    assert seen == ["one", "two"]
