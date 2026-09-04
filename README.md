# Hsx

Hsx is a local-first creative workspace for an AI agent. It combines an auto-discovered skill architecture with a Claude-inspired chat interface, clear file zones, persistent memory, and an explicit artifact handoff.

## Run it

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py
```

Open `http://127.0.0.1:8000`. Add `ANTHROPIC_API_KEY` to `.env` for the full LLM loop. Without a key, the workspace still runs with a small local fallback and the skills remain available for testing.

## Architecture

- `uploads/`: source material, treated as read-only by convention
- `scratch/`: temporary code and intermediate work
- `outputs/`: finished files shown in the Artifacts panel
- `memory/`: durable user/project facts
- `skills/*/skill.json + skill.py`: self-contained plugins discovered at startup

Adding a plugin does not require changes to the orchestrator. A skill only needs metadata in `skill.json` and a `run(**kwargs) -> dict` function in `skill.py`.