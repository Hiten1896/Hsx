from agent.music_catalog import MUSIC_CATALOG


def run(
    file_id: int,
    queue: list[int] | None = None,
    target_device: str = "client_browser",
) -> dict:
    if target_device not in {"client_browser", "local_host"}:
        raise ValueError("target_device must be local_host or client_browser.")
    return MUSIC_CATALOG.play(file_id, queue)
