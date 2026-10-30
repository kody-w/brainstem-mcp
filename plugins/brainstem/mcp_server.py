#!/usr/bin/env python3
"""The bridge's first entry point, kept working on purpose: launch.py, the Claude Code plugin and older
host configs all run `python mcp_server.py`. The bridge itself now lives in brainstem_mcp/server.py, next to
this file, so it can also install as the `brainstem-mcp` command. Importing this module gives that module.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brainstem_mcp import server  # noqa: E402

if __name__ == "__main__":
    sys.exit(server.main(sys.argv[1:]))
sys.modules[__name__] = server
