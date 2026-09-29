# brainstem-mcp

Talk to your local [RAPP Brainstem](https://github.com/kody-w/rapp-installer) from Claude Code.

Built on top of the Brainstem, never inside it: this repo only talks to a Brainstem you already
installed, over its normal `/chat` and `/health`.

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

Two tools Claude Code can call:

| Tool | What it does |
|---|---|
| `chat` | Send the Brainstem a message; it picks its own agents. Pass back `session_id` to continue the same conversation. |
| `capabilities` | The Brainstem's status, version, model and loaded agents. |

If the Brainstem is down or refuses a message, the call fails with a plain explanation.

## Settings

| Variable | Default | Use |
|---|---|---|
| `BRAINSTEM_URL` | `http://127.0.0.1:7071` | A Brainstem on another port |
| `BRAINSTEM_SECRET` | none | A Brainstem in LAN mode |
| `BRAINSTEM_MCP_TIMEOUT` | `240` | Seconds to wait for one answer |

## Other AIs

The bridge is a normal MCP server. Any MCP host can start it with
`python3 plugins/brainstem/launch.py`.

## License

MIT
