from __future__ import annotations


def ask_job_payload(job: dict | None) -> dict | None:
    if job is None:
        return None
    payload = {
        key: job[key]
        for key in (
            "id", "state", "question", "options", "allow_free_text", "question_count",
            "expires_at", "result", "error", "target_device", "device_actions", "approval_id",
        )
        if key in job
    }
    approval = job.get("approval") or {}
    if job.get("state") == "waiting_for_approval":
        payload["approval"] = {
            key: approval[key]
            for key in ("summary", "why", "risk", "details")
            if key in approval
        }
    return payload
