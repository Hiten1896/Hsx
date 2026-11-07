# Permissions

Hsx uses one permission rule. `config/permissions.yaml` lists action patterns that ask first. Actions outside that list run silently and receive an audit entry. The rule is editable in Settings > Permissions.

The default list asks before deleting or overwriting owner files, using sudo/root, editing `/etc` or system services, shutting down or rebooting, piping `curl` or `wget` into a shell, reading `.env` or secret files, starting the camera or microphone from chat, installing packages, or sending data to services other than the configured LLM.

The code parses tool names, commands, paths, environment expansions, encoded shell commands, and symlinks to choose an action pattern. It does not trust a model label. The root broker uses the same `needs_permission()` categories and has a separate action allowlist; it no longer has a shadow/autonomy mode or tier labels. Always-allow patterns are stored in the same YAML file and can be removed or reset from Settings. Every decision and settings change is appended to `logs/audit.jsonl` without storing argument values.

Hsx always denies attempts to expose API keys or bearer tokens, disable the audit log, or remove the permission system. Approval cannot override those denies. Pending approvals are stored, survive restarts, and expire with an explanatory note.

Turning on the camera in the web UI grants snapshot consent only to that browser session. Its camera lease is released when the UI turns it off, hides the tab, or reaches its five-minute limit. A chat request from a different session still needs approval.
