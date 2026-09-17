from video_tools.pipeline import create_video_job


def run(
    prompt: str,
    source_files: list | None = None,
    output_filename: str = "edited_video.mp4",
    trim_start: float | None = None,
    trim_end: float | None = None,
    project_id: str = "video_skill_job",
) -> dict:
    job = create_video_job(
        project_id=project_id,
        prompt=prompt,
        source_files=source_files,
        output_filename=output_filename,
        trim_start=trim_start,
        trim_end=trim_end,
    )
    return {
        "success": True,
        "job_id": job["job_id"],
        "status": job["status"],
        "project_id": job["project_id"],
        "message": "Video edit queued. Poll the job status endpoint for progress.",
    }
