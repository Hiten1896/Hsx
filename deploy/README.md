# Hsx deployment

The policy broker is a separate root-owned service. Copy `policy.yaml` to
`/etc/hsx/policy.yaml` with owner `root:root` and mode `0440`, copy
`hsx-broker.service` to `/etc/systemd/system/`, and enable it only after
installing the agent and creating the `hsx` user. The broker socket is
`/run/hsx/broker.sock`; the agent never receives sudo or a root shell.

On Debian/Ubuntu, install Python 3.11+, create a dedicated `hsx` user, create `/opt/hsx/venv`, install `requirements.txt`, and either set `HSX_AUTH_TOKEN` or securely capture the generated token from the first startup. Set `HSX_OWNER_PIN` during setup, or securely capture the generated owner PIN from the first startup. The PIN hash is stored outside the workspace and is required for self-improvement approval, promotion, and confirm-tier actions. Install Docker for the `run_code` sandbox; Docker is the only strong isolation mode.

Set `HSX_WORKSPACE=/home/hsxshare` and `HSX_WORK_DIR=Hsx Work` in `/opt/hsx/.env`. Create `Hsx Work` from the Samba owner account before starting Hsx so that it retains the intended ownership. Grant `hsx` read/traverse ACLs on the share and write ACLs only on `Hsx Work` (the README includes example `setfacl` commands). Verify with `getfacl`; do not solve write failures by granting the agent write access to the share root.

Install `deploy/hsx.service` with systemd. To enable Web Push, run `sudo sh deploy/generate-vapid.sh` after installing the requirements. This creates `/etc/hsx/secrets.env` with VAPID keys and `root:hsx` ownership, mode `0640`; the service loads it as an optional environment file. Keep Hsx bound to loopback and expose HTTPS through Tailscale Serve; enable HTTPS certificates for the tailnet device in the Tailscale admin console. This adds no listening port. Brave requires **Use Google Services for push messaging** to receive Web Push; Chrome and Edge work by default.

The boot installer also installs and enables the `hsx-update.timer`, which checks hourly.
Updates are disabled unless `.env` sets `HSX_AUTO_UPDATE=1` and
`HSX_UPDATE_SIGNER_FINGERPRINT` to the exact GPG signing-key fingerprint whose public key
is present in the service account's GPG keyring. Commits must be signed by that key;
unsigned, invalidly signed, untrusted, non-fast-forward, dirty-tree, or wrong-origin
updates are rejected. Set `HSX_UPDATE_REMOTE` for a trusted fork. The updater runs as the HSX service account
without sudo, requires write access to the checkout and virtual environment, updates
Python dependencies only when a root-level `requirements*.txt` changes, and runs
`npm ci --ignore-scripts` only when the Node dependency manifests change. After successful
updates it signals the running server to drain requests and re-exec. It does not update
Docker deployments. Review and test commits before signing; a trusted signature is the
authorization to deploy code.

For optional container deployment, run `docker compose -f deploy/docker-compose.yml up -d --build`. Back up `memory/`, `data/`, and the shared `Hsx Work/` results, then update by pulling the repository, reinstalling requirements, and restarting the service.
