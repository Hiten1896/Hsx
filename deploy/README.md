# Hsx deployment

The policy broker is a separate root-owned service. Copy `policy.yaml` to
`/etc/hsx/policy.yaml` with owner `root:root` and mode `0440`, copy
`hsx-broker.service` to `/etc/systemd/system/`, and enable it only after
installing the agent and creating the `hsx` user. The broker socket is
`/run/hsx/broker.sock`; the agent never receives sudo or a root shell.

On Debian/Ubuntu, install Python 3.11+, create a dedicated `hsx` user, create `/opt/hsx/.venv`, install `requirements.txt`, and either set `HSX_AUTH_TOKEN` or securely capture the generated token from the first startup. Set `HSX_OWNER_PIN` during setup, or securely capture the generated owner PIN from the first startup. The PIN hash is stored outside the workspace and is required for self-improvement approval, promotion, and confirm-tier actions. Install Docker for the `run_code` sandbox; Docker is the only strong isolation mode. Then install `deploy/hsx.service` with systemd.

For optional container deployment, run `docker compose -f deploy/docker-compose.yml up -d --build`. Back up `memory/`, `data/`, and `outputs/` and update by pulling the repository, reinstalling requirements, and restarting the service.
