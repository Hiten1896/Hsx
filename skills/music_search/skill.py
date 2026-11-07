from agent.music_catalog import MUSIC_CATALOG


def run(query: str) -> dict:
    return MUSIC_CATALOG.search(query)
