# brainstem-mcp

Your local [RAPP Brainstem](https://github.com/kody-w/rapp-installer) as an MCP server, for Claude Code
or any other MCP app.

Built on top of the Brainstem, never inside it: this repo only talks to a Brainstem you already
installed, over its normal `/chat` and `/health`.

**Use `/chat` directly when you can.** Anything that can make an HTTP call should send
`POST /chat` to the Brainstem itself. This bridge is for hosts that can only speak MCP. Going
through a host costs the host's own context on every model call: in
[token-bench](https://github.com/kody-w/token-bench) ([The Context Tax](https://kody-w.github.io/token-bench/)), the same
business questions cost about 6-13k tokens a turn through `/chat` and 76-162k a turn through
Copilot CLI and this bridge.

## Install

You need a running Brainstem first:

```bash
curl -fsSL https://kody-w.github.io/rapp-installer/install.sh | bash
```

### As a Claude Code plugin

In Claude Code:

```
/plugin marketplace add kody-w/brainstem-mcp
/plugin install brainstem@brainstem-mcp
/reload-plugins
/brainstem:setup
```

`/brainstem:setup` checks that the Brainstem is running and signed in, and prepares the bridge
(a small Python environment in `~/.cache/brainstem-mcp`, about a minute the first time). If it
built anything, run `/reload-plugins` once more.

Until setup has run, the plugin offers Claude a single `setup` tool, so you can also just ask
Claude to "set up the Brainstem".

### As a plain MCP server, in any MCP app

No plugin needed. One line per app. Each one starts the `brainstem-mcp` command with
[uv](https://docs.astral.sh/uv/), which fetches it from this repo the first time.

Claude Code:

```
claude mcp add brainstem -- uvx --from git+https://github.com/kody-w/brainstem-mcp brainstem-mcp
```

Codex (not yet tested):

```
codex mcp add brainstem -- uvx --from git+https://github.com/kody-w/brainstem-mcp brainstem-mcp
```

Antigravity (not yet tested): add the JSON below to `~/.gemini/antigravity/mcp_config.json`.

Any MCP app (not yet tested beyond Claude Code):

```json
{
  "mcpServers": {
    "brainstem": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/kody-w/brainstem-mcp", "brainstem-mcp"]
    }
  }
}
```

Rather not use uv? `pip install git+https://github.com/kody-w/brainstem-mcp` gives you the same
`brainstem-mcp` command. From a clone, `python3 plugins/brainstem/mcp_server.py` works too, once
`mcp` and `requests` are installed. A Brainstem on another port: set `BRAINSTEM_URL` in the app's
server settings (`--env BRAINSTEM_URL=...` for Claude Code and Codex).

## What you get

Three tools any MCP app can call:

| Tool | What it does |
|---|---|
| `chat` | Send the Brainstem a message; it picks its own agents. Pass back `session_id` to continue the same conversation. For work that may take minutes, pass `wait: false`. |
| `job_status` | The answer to a `wait: false` chat, once it is done. |
| `capabilities` | The Brainstem's status, version, model and loaded agents. |

If the Brainstem is down or refuses a message, the call fails with a plain explanation.

At startup the bridge reads the Brainstem's loaded agents and names them in the `chat` tool's
description, so the host can see which requests to hand over instead of doing the work itself.
Agents loaded later appear after the host restarts the bridge.

## Settings

| Variable | Default | Use |
|---|---|---|
| `BRAINSTEM_URL` | `http://127.0.0.1:7071` | A Brainstem on another port |
| `BRAINSTEM_SECRET` | none | A Brainstem in LAN mode |
| `BRAINSTEM_MCP_TIMEOUT` | `240` | Seconds a waiting `chat` waits |
| `BRAINSTEM_MCP_JOB_TIMEOUT` | `3600` | Seconds a `wait: false` chat may run |

## brainstem-mcp or RAPPMCP?

- **brainstem-mcp** is your grail Brainstem as an MCP server. You talk to it with `chat`; it picks
  its own agents and keeps your sessions. Soon it can also offer each of its agents as its own tool.
- **[RAPPMCP](https://github.com/kody-w/rappmcp)** is the full RAPP ecosystem as MCP: a Brainstem
  distro built on brainstem-mcp.

A distro adds no engine of its own. It is a pinned kernel plus a list of `agent.py` files, each pinned
by its checksum, dropped into the Brainstem's `agents/` folder and served by brainstem-mcp. The grail
itself is never edited.

Tool names follow one rule in both: letters, digits, `_` and `-`, at most 64 characters. `chat`,
`job_status`, `capabilities` and `setup` belong to the Brainstem, so no agent tool takes them.

## License

MIT
