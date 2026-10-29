import ipaddress
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
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
ENV_FILE = BASE_DIR / ".env"
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
PANEL_MODE = os.getenv("HSX_PANEL_MODE", "off").strip().lower()
PANEL_PROVIDERS = tuple(
    item.strip().lower()
    for item in os.getenv("HSX_PANEL_PROVIDERS", "gemini,groq,openrouter").split(",")
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
AUTH_TOKEN_FILE = DATA_DIR / "auth.token"


def _load_or_create_auth_token() -> str:
    global AUTH_TOKEN_GENERATED
    configured = os.getenv("HSX_AUTH_TOKEN", "").strip()
    if configured:
        AUTH_TOKEN_GENERATED = False
        return configured
    token = secrets.token_urlsafe(32)
    AUTH_TOKEN_GENERATED = True
    existing = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    lines = [line for line in existing.splitlines() if not line.startswith("HSX_AUTH_TOKEN=")]
    ENV_FILE.write_text("\n".join([*lines, f"HSX_AUTH_TOKEN={token}"]) + "\n", encoding="utf-8")
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass
    return token


AUTH_TOKEN = _load_or_create_auth_token()
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