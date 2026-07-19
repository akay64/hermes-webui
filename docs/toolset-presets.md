# Toolset Presets

Toolset Presets are profile-scoped, saved exact allowlists for ordinary WebUI
chats. They make the composer toolset selector reusable without changing the
tool restrictions of cron jobs, background agents, subagents, recovery runs, or
existing conversations.

## Using presets

Open **Toolsets** in the composer, select built-in tools and enabled MCP
servers, then choose **Save current as preset**. A preset can be renamed,
updated from the currently displayed allowlist, deleted, or made the default
for new chats.

The default applies only when a normal new-chat request does not contain an
explicit toolset choice. The wire behavior is intentionally three-valued:

- omitted `enabled_toolsets`: copy the saved default preset, if configured;
- `enabled_toolsets: null`: use the active profile's configured defaults;
- `enabled_toolsets: [...]`: copy that exact allowlist into the new session.

Changing or deleting a preset never changes an existing session. If tools are
changed after a conversation has messages, WebUI warns that the agent must be
rebuilt and prompt-cache reuse may be reduced. Starting a new chat with the
preset is the primary action.

## MCP servers

An MCP server is represented by its configured server name. For example, a
preset containing `ticktick` selects the TickTick server toolset; individual
TickTick operations do not need to be listed. Hermes registers the tools the
server exposes after applying its configured `tools.exclude` rules.

The MCP server must be globally enabled in `config.yaml`. A disabled or invalid
server is shown as unavailable with guidance to enable it in MCP Settings. A
preset that references a disabled or unknown toolset is marked **needs
attention** and cannot be applied; WebUI never falls back to all tools.

## Storage

Presets are stored as versioned JSON under the active profile:

```text
<HERMES_HOME>/webui/toolset_presets.json
```

Writes use a flushed temporary sibling followed by an atomic replace. The file
contains stable IDs, labels, exact deduplicated non-empty toolset arrays,
timestamps, and an optional `default_preset_id`. It contains no MCP headers,
environment variables, API keys, or other server configuration secrets.

Removing the file restores the historical behavior: ordinary new chats use the
active profile defaults unless the request supplies an explicit allowlist.
