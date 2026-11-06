import ipaddress
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
LOGGER = logging.getLogger("hsx.config")

load_dotenv(ENV_FILE)

_DEPRECATED_MODEL_IDS = {
    ("GEMINI_MODEL", "gemini-2.0-flash"): "gemini-3.6-flash",
    ("GEMINI_MODEL", "gemini-2.0-flash-001"): "gemini-3.6-flash",
    ("OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free"): "google/gemini-3.6-flash",
}
_WARNED_DEPRECATED_MODELS: set[tuple[str, str]] = set()


def model_setting(name: str, default: str) -> str:
    configured = os.getenv(name, default).strip()
    replacement = _DEPRECATED_MODEL_IDS.get((name, configured))
    if replacement:
        key = (name, configured)
        if key not in _WARNED_DEPRECATED_MODELS:
            LOGGER.warning(
                "Configured model %r for %s is retired; using supported model %r.",
                configured,
                name,
                replacement,
            )
            _WARNED_DEPRECATED_MODELS.add(key)
        return replacement
    return configured or default

WORKSPACE_DIR = Path(
    os.getenv("HSX_WORKSPACE", str(BASE_DIR if os.name == "nt" else "/home/hsxshare"))
).expanduser().resolve()
WORK_DIR = Path(os.getenv("HSX_WORK_DIR", "Hsx Work")).expanduser()
if not WORK_DIR.is_absolute():
    WORK_DIR = WORKSPACE_DIR / WORK_DIR
WORK_DIR = WORK_DIR.absolute()
if WORK_DIR == WORKSPACE_DIR:
    raise ValueError("HSX_WORK_DIR must be a dedicated subdirectory of HSX_WORKSPACE.")
if WORKSPACE_DIR not in WORK_DIR.parents:
    raise ValueError("HSX_WORK_DIR must remain inside HSX_WORKSPACE.")
OUTPUTS_DIR = WORK_DIR
SCRATCH_DIR = BASE_DIR / "scratch"
UPLOADS_DIR = BASE_DIR / "uploads"
MEMORY_DIR = BASE_DIR / "memory"
DATA_DIR = BASE_DIR / "data"
MEMORY_DB = DATA_DIR / "memory.db"
JOBS_DB = DATA_DIR / "jobs.db"
IMPROVEMENTS_DB = DATA_DIR / "improvements.db"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
MODEL = os.getenv("HSX_MODEL", "claude-sonnet-4-6").strip()
PROVIDER = os.getenv("HSX_PROVIDER", "").strip().lower()
FALLBACKS = tuple(
    item.strip().lower()
    for item in os.getenv("HSX_FALLBACKS", "").split(",")
    if item.strip()
)
PANEL_MODE = os.getenv("HSX_PANEL_MODE", "off").strip().lower()
PANEL_PROVIDERS = tuple(
    item.strip().lower()
    for item in os.getenv("HSX_PANEL_PROVIDERS", "gemini,groq,openrouter").split(",")
    if item.strip()
)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = model_setting("GEMINI_MODEL", "gemini-3.6-flash")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip()
GROK_API_KEY = (os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") or "").strip()
GROK_MODEL = (os.getenv("GROK_MODEL") or os.getenv("XAI_MODEL") or "grok-3-mini").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = model_setting("OPENROUTER_MODEL", "google/gemini-3.6-flash")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_COMPATIBLE_URL = os.getenv("OPENAI_COMPATIBLE_URL", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODE = os.getenv("HSX_MODE", "jarvis").strip().lower()
HOST = os.getenv("HSX_HOST", "0.0.0.0").strip() or "0.0.0.0"
PORT = int(os.getenv("HSX_PORT", "8000"))
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


for directory in (WORKSPACE_DIR, OUTPUTS_DIR, SCRATCH_DIR, UPLOADS_DIR, MEMORY_DIR, DATA_DIR):
    directory.mkdir(parents=True, exist_ok=True)
