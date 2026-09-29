---
description: Check whether your local Brainstem is ready to use from Claude Code, and prepare the bridge if needed
allowed-tools: Bash(python3:*)
---

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/launch.py" --setup
```

Present the result to the user in plain words, not as JSON:
- If `ready` is true, say the Brainstem is ready and name its version, model and how many agents it has.
  If `actionsTaken` is not empty, tell them to run `/reload-plugins` once so Claude Code starts the bridge.
- If `ready` is false, give the `nextSteps` exactly, one per line, most important first.
  Keep the install command verbatim so it can be copied.
- Do not run the Brainstem installer or sign in on the user's behalf.
