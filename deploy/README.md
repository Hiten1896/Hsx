# Hsx deployment

On Debian/Ubuntu, install Python 3.11+, create a dedicated `hsx` user, create `/opt/hsx/.venv`, install `requirements.txt`, and either set `HSX_AUTH_TOKEN` or securely capture the generated token from the first startup. Set `HSX_OWNER_PIN` during setup, or securely capture the generated owner PIN from the first startup. The PIN hash is stored outside the workspace and is required for self-improvement approval, promotion, and confirm-tier actions. Install Docker for the `run_code` sandbox; Docker is the only strong isolation mode. Then install `deploy/hsx.service` with systemd.

For optional container deployment, run `docker compose -f deploy/docker-compose.yml up -d --build`. Back up `memory/`, `data/`, and `outputs/` and update by pulling the repository, reinstalling requirements, and restarting the service.
