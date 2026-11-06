# Autonomy and approvals

Hsx classifies actions in code before execution. The model's tool description or
requested risk label does not override the policy. `HSX_AUTONOMY` accepts
`auto`, `ask`, or `strict`; the default is `auto`.

| Tier | Examples | `auto` | `ask` | `strict` |
| --- | --- | --- | --- | --- |
| SAFE | Read-only system facts, workspace reads/search, creating a new file in the workspace | Run and audit | Run and audit | Run and audit |
| NORMAL | Workspace writes, package installs in the venv, normal outbound HTTP, restarting `hsx` | Run and audit | Ask first | Run and audit |
| DANGEROUS | Deletion/overwrite, `sudo`, system settings, `/etc`, SSH/firewall/Tailscale/Samba changes, secrets, camera/microphone, reboot/shutdown | Ask every time | Ask every time | Ask every time |
| BLOCKED | Exposing tokens/secrets, disabling the audit log, removing the approval system | Never run | Never run | Never run |

Set the mode in `.env`, for example `HSX_AUTONOMY=ask`. Commands are parsed and
paths are resolved before classification. Unclear shell syntax is treated
conservatively. Workspace paths are checked after symlink resolution. The
`system_info`, `disk_usage`, `network_status`, and `list_usb` tools inspect the
machine running the server; they do not dispatch work to a mesh node. Use
`create_text_file` for a new workspace file; a name collision gets a numeric
suffix rather than overwriting the existing file.

## Approval flow

When approval is required, Hsx stores the action, arguments, provider-safe
conversation checkpoint, approval ID, and expiry in the SQLite job database.
The CLI and browser render a human-readable summary, reason, risk level, and
optional redacted details. Raw approval payloads, choices, and advisory fields
are not sent to the model or rendered to users.

Reply with `yes`, `y`, `ok`, `proceed`, `approve`, `i understand`, or a close
spelling; reply with `no`, `n`, `cancel`, or `stop` to decline. The next message
for a session with a pending approval is handled as the answer first. An
unrecognized response leaves the action pending and repeats the summary.
Approvals expire after 24 hours; expired actions are not executed. Pending
approvals survive server restarts, and the stored tool call is resumed rather
than replaying the original prompt.

The audit log records the action tier, decision, and approver without storing
argument values. Provider failures are shown in plain English; provider response
bodies are restricted to server logs and `HSX_DEBUG=1`.
