from agent.memory import MemoryManager
from config import MEMORY_DB


def run(text: str = "", query: str = "") -> dict:
    manager = MemoryManager(MEMORY_DB)
    if text.strip():
        return {"success": True, "note": manager.add_fact(text, category="note", source="notes")}
    return {"success": True, "notes": manager.search(query or "note", limit=20)}
