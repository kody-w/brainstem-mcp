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

## Each agent as its own tool

With `--agents`, the bridge also serves every agent in your Brainstem's `agents` folder as its own
MCP tool, next to `chat`, `job_status` and `capabilities`:

```
claude mcp add brainstem -- uvx --from git+https://github.com/kody-w/brainstem-mcp brainstem-mcp --agents
```

That serves `~/.brainstem/src/rapp_brainstem/agents`. Give a folder to serve another one:
`brainstem-mcp --agents /path/to/my-agents`. It loads agents the way the Brainstem does (every
`*_agent.py`, sorted by name), and the host sees the tool list change when you add, change or remove
a file. The agents run here, so they answer even when the Brainstem is not running.

Each file is scanned, and each call runs, in its own short-lived process with a timeout, so a broken
agent cannot stop the server or hide the other tools. A stopped call takes whatever it started with
it, on Linux, macOS and Windows. It uses the grail's `basic_agent.py` unchanged and only reads it:
`brainstem_mcp/kernel.json` pins it by commit and sha256. If your Brainstem has that exact file it is
used; if not, it is fetched once from the grail at the pinned commit, checked, and kept in
`~/.cache/brainstem-mcp`. Nothing is ever written into your Brainstem, not even `__pycache__`.

| Option | Default | Use |
|---|---|---|
| `--timeout S` | `120` | Seconds one agent call may run before it is stopped |
| `--scan-timeout S` | `10` | Seconds one agent file may take to load before it is skipped |
| `--kernel PATH` | the pinned file | Use this `basic_agent.py` |

Agent tools follow the naming rule below; an agent named `chat`, `job_status`, `capabilities` or
`setup` is skipped with a log line.

## Settings

| Variable | Default | Use |
|---|---|---|
| `BRAINSTEM_URL` | `http://127.0.0.1:7071` | A Brainstem on another port |
| `BRAINSTEM_SECRET` | none | A Brainstem in LAN mode |
| `BRAINSTEM_MCP_TIMEOUT` | `240` | Seconds a waiting `chat` waits |
| `BRAINSTEM_MCP_JOB_TIMEOUT` | `3600` | Seconds a `wait: false` chat may run |

## brainstem-mcp or RAPPMCP?

- **brainstem-mcp** is your grail Brainstem as an MCP server, and the one engine. You talk to it with
  `chat`; it picks its own agents and keeps your sessions. With `--agents` it also offers each agent
  as its own tool.
- **[RAPPMCP](https://github.com/kody-w/rappmcp)** is the full RAPP ecosystem as MCP: a Brainstem
  distro built on brainstem-mcp.

A distro adds no engine of its own. It is a pinned kernel plus a list of `agent.py` files, each pinned
by its checksum, served by brainstem-mcp. The grail itself is never edited. Serve one with
`brainstem-mcp --distro distro.json`:

```json
{
  "schema": "brainstem-distro/1",
  "name": "my-distro",
  "kernel": {"repository": "https://github.com/kody-w/rapp-installer", "commit": "<40 hex>",
             "files": [{"path": "rapp_brainstem/agents/basic_agent.py", "sha256": "<64 hex>"}]},
  "agents": [{"path": "agents/my_agent.py", "sha256": "<64 hex>"}]
}
```

Paths are relative to the manifest. A file whose bytes do not match its pin is refused, at startup and
again on every call. `python -m brainstem_mcp.distro check distro.json` says which files match;
`python -m brainstem_mcp.distro pin distro.json` writes their current checksums. `--distro` and
`--agents` work together: the distro's agents come first.

Keep pinned files byte-exact in git: put `agents/*.py -text` in the distro repo's `.gitattributes`.
Without it, a Windows checkout turns line endings into CRLF, the checksums change, and the files are
refused.

Tool names follow one rule in both: letters, digits, `_` and `-`, at most 64 characters. `chat`,
`job_status`, `capabilities` and `setup` belong to the Brainstem, so no agent tool takes them.

## License

MIT
