# Hsx deployment

The policy broker is a separate root-owned service. Copy `policy.yaml` to
`/etc/hsx/policy.yaml` with owner `root:root` and mode `0440`, copy
`hsx-broker.service` to `/etc/systemd/system/`, and enable it only after
installing the agent and creating the `hsx` user. The broker socket is
`/run/hsx/broker.sock`; the agent never receives sudo or a root shell.

On Debian/Ubuntu, install Python 3.11+, create a dedicated `hsx` user, create `/opt/hsx/venv`, and install `requirements.txt`. The server binds to loopback by default. Keep it there and use Tailscale Serve for remote browser access; add the exact Tailscale HTTPS origin to `HSX_ALLOWED_ORIGINS`. Browser requests from other origins are rejected, and requests without an `Origin` header require the bearer token in `/opt/hsx/data/auth.token` (unless `HSX_AUTH_TOKEN` is configured). Do not publish the server directly to the public internet. Set `HSX_OWNER_PIN` during setup, or securely capture the generated owner PIN from the first startup. The PIN hash is stored outside the workspace and is required for self-improvement approval and promotion. Install Docker for the `run_code` sandbox; Docker is the only strong isolation mode.

For host audio playback, install the reviewed OS packages listed in
`deploy/audio-packages.txt` with administrator approval (for example,
`xargs -r sudo apt-get install -y < deploy/audio-packages.txt`) and install
`requirements-media.txt` in the Hsx virtual environment for `yt-dlp`. Add the
`hsx` service account to the `audio` group when using ALSA. The service unit
includes that supplementary group; `run_hsx.sh` sets `XDG_RUNTIME_DIR` to
`/run/user/<uid>` only when that runtime directory exists. Host playback
reports unavailable if `mpv` or an active PulseAudio sink / ALSA sound card
cannot be found; installing a package does not prove that speakers are
connected or audible.

Set `HSX_WORKSPACE=/home/hsxshare` and `HSX_WORK_DIR=Hsx Work` in `/opt/hsx/.env`. Create `Hsx Work` from the Samba owner account before starting Hsx so that it retains the intended ownership. Grant `hsx` read/traverse ACLs on the share and write ACLs only on `Hsx Work` (the README includes example `setfacl` commands). Verify with `getfacl`; do not solve write failures by granting the agent write access to the share root.

Install `deploy/hsx.service` with systemd. To enable Web Push, run `sudo sh deploy/generate-vapid.sh` after installing the requirements. This creates `/etc/hsx/secrets.env` with VAPID keys and `root:hsx` ownership, mode `0640`; the service loads it as an optional environment file. Keep Hsx bound to loopback. Brave requires **Use Google Services for push messaging** to receive Web Push; Chrome and Edge work by default.

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

The included Docker Compose port mapping exposes the container listener and is not
covered by the loopback-only systemd deployment guidance; do not use it to publish
the API. Back up `memory/`, `data/`, and the shared `Hsx Work/` results before
updating the installation.
