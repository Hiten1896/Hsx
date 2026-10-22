import ipaddress
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
OUTPUTS_DIR = BASE_DIR / "outputs"
SCRATCH_DIR = BASE_DIR / "scratch"
UPLOADS_DIR = BASE_DIR / "uploads"
MEMORY_DIR = BASE_DIR / "memory"
DATA_DIR = BASE_DIR / "data"
MEMORY_DB = DATA_DIR / "memory.db"
JOBS_DB = DATA_DIR / "jobs.db"
IMPROVEMENTS_DB = DATA_DIR / "improvements.db"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("HSX_MODEL", "claude-sonnet-4-6")
PROVIDER = os.getenv("HSX_PROVIDER", "anthropic").strip().lower()
FALLBACKS = tuple(
    item.strip().lower()
    for item in os.getenv("HSX_FALLBACKS", "ollama").split(",")
    if item.strip()
)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_COMPATIBLE_URL = os.getenv("OPENAI_COMPATIBLE_URL", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODE = os.getenv("HSX_MODE", "jarvis").strip().lower()
HOST = os.getenv("HSX_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("HSX_PORT", "8000"))
AUTH_TOKEN = os.getenv("HSX_AUTH_TOKEN", "")
ALLOWED_ORIGINS = tuple(
    origin.strip()
    for origin in os.getenv(
        "HSX_ALLOWED_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000",
    ).split(",")
    if origin.strip()
)
SANDBOX = os.getenv("HSX_SANDBOX", "auto").strip().lower()


def is_loopback_host(host: str) -> bool:
    """Return whether a configured host is a loopback address or name."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


if not is_loopback_host(HOST) and not AUTH_TOKEN:
    raise RuntimeError("HSX_AUTH_TOKEN is required when HSX_HOST is not loopback.")

for directory in (OUTPUTS_DIR, SCRATCH_DIR, UPLOADS_DIR, MEMORY_DIR, DATA_DIR):
    directory.mkdir(exist_ok=True)