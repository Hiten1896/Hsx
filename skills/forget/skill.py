from agent.memory import MemoryManager
from config import MEMORY_DB


def run(id: int) -> dict:
    MemoryManager(MEMORY_DB).delete_fact(id)
    return {"success": True, "deleted": id}
