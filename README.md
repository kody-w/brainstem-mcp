# brainstem-mcp

Talk to your local [RAPP Brainstem](https://github.com/kody-w/rapp-installer) from Claude Code.

Built on top of the Brainstem, never inside it: this repo only talks to a Brainstem you already
installed, over its normal `/chat` and `/health`.

**Use `/chat` directly when you can.** Anything that can make an HTTP call should send
`POST /chat` to the Brainstem itself. This bridge is for hosts that can only speak MCP. Going
through a host costs the host's own context on every model call: in token-bench, the same
business questions cost about 6-13k tokens a turn through `/chat` and 76-162k a turn through
Copilot CLI and this bridge.

## Install

You need a running Brainstem first:

```bash
curl -fsSL https://kody-w.github.io/rapp-installer/install.sh | bash
```

Then, in Claude Code:

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

## What you get

Three tools Claude Code can call:

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

## Other AIs

The bridge is a normal MCP server. Any MCP host can start it with
`python3 plugins/brainstem/launch.py`.

## License

MIT
