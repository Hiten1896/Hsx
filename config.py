import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
OUTPUTS_DIR = BASE_DIR / "outputs"
SCRATCH_DIR = BASE_DIR / "scratch"
UPLOADS_DIR = BASE_DIR / "uploads"
MEMORY_DIR = BASE_DIR / "memory"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("HSX_MODEL", "claude-sonnet-4-6")

for directory in (OUTPUTS_DIR, SCRATCH_DIR, UPLOADS_DIR, MEMORY_DIR):
    directory.mkdir(exist_ok=True)