# MCP host setup

Arbitype exposes a local MCP STDIO server. The setup command can add that
server to supported host configuration files without copying the API key into
those files.

The generated entry runs `uvx arbitype`, which resolves the upstream PyPI
distribution. It does not install or run fork-only changes from this source
checkout. To use this fork's experimental image support, configure the host to
run `python3 -B` with the absolute path to this checkout's `server.py`, or use
`uvx --from 'git+https://github.com/Melivo/typesafe-mcp-arbitype.git@<full-commit>' arbitype`.
Replace the placeholder with a full commit ID. Keep `TYPESAFE_API_KEY` in the
host process environment; the [README quick start](../README.md#quick-start)
shows the source-checkout command.

## Supported hosts

| Host | Configuration | Managed format |
| --- | --- | --- |
| Codex | ~/.codex/config.toml | mcp_servers.arbitype |
| Claude Code or Claude Desktop | ~/.claude.json or the platform's Claude config | mcpServers.arbitype |
| Cursor | ~/.cursor/mcp.json | mcpServers.arbitype |
| VS Code | User mcp.json | servers.arbitype plus a password input |

The exact path is selected from the host executable, existing configuration,
and the current platform. Use a dry run to inspect the selected file before
applying changes.

## Safe setup flow

Detect every supported host that is present:

~~~bash
arbitype setup --detect --dry-run
~~~

The command prints every detected target, a plan, and a unified diff. When
multiple hosts are detected, all targets are listed and the write decision is
made once for the complete plan.

Apply one host interactively:

~~~bash
arbitype setup codex
arbitype setup claude
arbitype setup cursor
arbitype setup vscode
~~~

The write flow is:

~~~text
plan → unified diff → interactive confirmation → backup → apply
~~~

In a terminal, Arbitype asks for confirmation with [y/N]. In a non-interactive
environment, it refuses to write unless explicit confirmation is supplied:

~~~bash
arbitype setup codex --yes
~~~

The --yes flag is intended for an already reviewed plan. It is not needed for
--dry-run, which never writes files or creates backups.

## Host-native credential forwarding

The generated server always launches:

~~~text
command: uvx
args: [arbitype]
~~~

Credential forwarding is host-native and references the process environment:

### Codex

~~~toml
[mcp_servers.arbitype]
command = "uvx"
args = ["arbitype"]
env_vars = ["TYPESAFE_API_KEY"]
~~~

### Claude

~~~json
{
  "mcpServers": {
    "arbitype": {
      "type": "stdio",
      "command": "uvx",
      "args": ["arbitype"],
      "env": {
        "TYPESAFE_API_KEY": "${TYPESAFE_API_KEY}"
      }
    }
  }
}
~~~

### Cursor

~~~json
{
  "mcpServers": {
    "arbitype": {
      "type": "stdio",
      "command": "uvx",
      "args": ["arbitype"],
      "env": {
        "TYPESAFE_API_KEY": "${env:TYPESAFE_API_KEY}"
      }
    }
  }
}
~~~

### VS Code

~~~json
{
  "servers": {
    "arbitype": {
      "type": "stdio",
      "command": "uvx",
      "args": ["arbitype"],
      "env": {
        "TYPESAFE_API_KEY": "${input:typesafe-api-key}"
      }
    }
  },
  "inputs": [
    {
      "type": "promptString",
      "id": "typesafe-api-key",
      "description": "TypeSafe API key for Arbitype",
      "password": true
    }
  ]
}
~~~

Host configuration syntax can evolve. Prefer Arbitype's dry run and the
host's current documentation over copying a stale example. The setup command
does not write a key value, and the VS Code input is marked as a password.

## Backups, ownership, and idempotency

- An existing configuration file is copied to a timestamped
  .arbitype-backup file before it is changed.
- Unknown keys and other MCP servers are preserved.
- A pre-existing arbitype entry that is not known to be managed by Arbitype
  is treated as a conflict and is never overwritten.
- JSON hosts use a small ownership sidecar. Codex uses explicit managed
  comment markers.
- Repeating setup after a successful apply is a no-op.
- remove deletes only an entry whose exact content and ownership marker match
  what Arbitype created.

Remove an entry managed by Arbitype:

~~~bash
arbitype setup codex --remove
arbitype setup claude --remove
~~~

If the managed entry was edited, removal stops rather than deleting user
changes. A manually created or ambiguous arbitype entry must be removed or
resolved by its owner.

## Verification

After setup, inspect local configuration without making a provider request:

~~~bash
arbitype doctor --json
~~~

An explicit live check requires a valid process environment variable and may
incur provider usage:

~~~bash
TYPESAFE_API_KEY="your-key" arbitype doctor --live
~~~

Never place the key in an MCP tool argument, a committed configuration file,
or a shell command that will be copied into a shared log.
