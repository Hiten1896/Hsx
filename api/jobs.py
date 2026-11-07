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
    if job.get("state") in {"waiting_for_approval", "permission_request"}:
        payload["approval"] = {
            key: approval[key]
            for key in (
                "summary", "why", "risk", "details", "request_type",
                "command", "host_impact", "security_risk",
            )
            if key in approval
        }
    return payload
