# Hsx deployment

On Debian/Ubuntu, install Python 3.11+, create a dedicated `hsx` user, create `/opt/hsx/.venv`, install `requirements.txt`, copy `.env.example` to `/opt/hsx/.env`, and set a strong `HSX_AUTH_TOKEN` before binding beyond loopback. Install Docker for the `run_code` sandbox, then install `deploy/hsx.service` with systemd.

For optional container deployment, run `docker compose -f deploy/docker-compose.yml up -d --build`. Back up `memory/`, `data/`, and `outputs/` and update by pulling the repository, reinstalling requirements, and restarting the service.
