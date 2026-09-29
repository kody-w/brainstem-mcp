"""Contract: the bridge against the real, unchanged grail kernel (kody-w/rapp-installer, rapp_brainstem/).

The grail is the kernel; this repo is userland and may only rely on /chat and /health. These tests run the
bridge against a kernel started straight from the grail, signed out, so they need no secrets. If the grail
ever changes that contract, this fails here instead of in front of a user.

Run: GRAIL_KERNEL_URL=http://127.0.0.1:7071 python -m pytest tests/contract -q
"""
import importlib
import importlib.util
import json
import os
import pathlib
import sys
import time

import pytest

URL = os.getenv("GRAIL_KERNEL_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set GRAIL_KERNEL_URL to a running grail kernel")
PLUGIN = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "brainstem"


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setenv("BRAINSTEM_URL", URL)
    sys.path.insert(0, str(PLUGIN))
    module = importlib.reload(importlib.import_module("mcp_server"))
    monkeypatch.setattr(module, "BRAINSTEM_URL", URL.rstrip("/"))
    return module


def test_health_carries_the_fields_the_bridge_reads(bridge):
    health = json.loads(bridge.capabilities())
    assert set(health) == {"status", "version", "model", "agents", "quarantined"}
    assert health["status"] in ("ok", "unauthenticated") and health["version"]
    assert isinstance(health["agents"], list)


def test_chat_without_input_is_refused_in_plain_words(bridge):
    with pytest.raises(bridge.ToolError, match=r"HTTP 400\): user_input is required"):
        bridge.chat("")


def test_chat_passes_the_kernels_own_reason_through(bridge):
    with pytest.raises(bridge.ToolError, match="refused the request") as caught:
        bridge.chat("hello", session_id="contract")
    assert "Expecting value" not in str(caught.value) and "contract" not in bridge._histories


def test_chat_accepts_conversation_history_the_bridge_sends(bridge):
    bridge._histories["contract-h"] = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    with pytest.raises(bridge.ToolError) as caught:
        bridge.chat("hello", session_id="contract-h")
    assert "conversation_history" not in str(caught.value)


def test_background_turn_reports_the_kernels_reason(bridge):
    bridge.chat("hello", session_id="contract-bg", wait=False)
    for _ in range(100):
        report = json.loads(bridge.job_status("contract-bg"))
        if report["status"] != "running":
            break
        time.sleep(0.1)
    assert report["status"] in ("done", "error") and "Expecting value" not in report.get("error", "")


def test_setup_sees_the_kernel(monkeypatch):
    spec = importlib.util.spec_from_file_location("launch", PLUGIN / "launch.py")
    launch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch)
    monkeypatch.setattr(launch, "BRAINSTEM_URL", URL.rstrip("/"))
    found = launch._brainstem()
    assert found["reachable"] and found["version"] and isinstance(found["agents"], int)
