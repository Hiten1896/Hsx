from agent.memory import MemoryManager
from config import MEMORY_DB


def run(text: str, category: str = "general", pinned: bool = False) -> dict:
    try:
        return {"success": True, "fact": MemoryManager(MEMORY_DB).add_fact(text, category, pinned=pinned)}
    except (ValueError, OSError) as error:
        return {"success": False, "error": str(error)}
