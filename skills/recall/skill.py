from agent.memory import MemoryManager
from config import MEMORY_DB


def run(query: str, limit: int = 5) -> dict:
    return {"success": True, "facts": MemoryManager(MEMORY_DB).search(query, max(1, min(limit, 20)))}
