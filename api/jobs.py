from __future__ import annotations


def ask_job_payload(job: dict | None) -> dict | None:
    if job is None:
        return None
    return {
        key: job[key]
        for key in (
            "id", "state", "question", "options", "allow_free_text", "question_count",
            "expires_at", "result", "error", "target_device", "device_actions",
        )
    }
