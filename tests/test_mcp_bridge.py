import importlib
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "plugins" / "brainstem"
sys.path.insert(0, str(ROOT))


class Reply:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


@pytest.fixture
def bridge(monkeypatch):
    module = importlib.import_module("mcp_server")
    module._histories.clear()
    sent = []

    health = {"status": "ok", "version": "0.6.16", "model": "m", "agents": ["Twins"], "quarantined": [], "extra": 1}

    def request(method, url, headers=None, timeout=None, json=None):
        if method == "GET":
            return Reply(200, health)
        sent.append({"url": url, "body": json})
        text = f"answer {len(sent)}"
        return Reply(200, {"response": text, "model": "m", "agent_logs": "[Twins] Twin: Brainstem Spy"})

    monkeypatch.setattr(module.requests, "request", request)
    module.sent = sent
    return module


def fail_with(bridge, monkeypatch, outcome):
    def request(*args, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(bridge.requests, "request", request)


def test_chat_keeps_bounded_per_session_history_for_the_stateless_kernel(bridge, monkeypatch):
    first = json.loads(bridge.chat("hello", session_id="s1"))
    assert first == {"response": "answer 1", "model": "m", "agent_logs": "[Twins] Twin: Brainstem Spy", "session_id": "s1"}
    bridge.chat("again", session_id="s1")
    assert bridge.sent[0]["url"].endswith("/chat") and bridge.sent[0]["body"]["conversation_history"] == []
    assert bridge.sent[1]["body"]["conversation_history"] == [
        {"role": "user", "content": "hello"}, {"role": "assistant", "content": "answer 1"}]
    monkeypatch.setattr(bridge, "HISTORY_MESSAGES", 2)
    bridge.chat("third", session_id="s1")
    assert bridge._histories["s1"] == [{"role": "user", "content": "third"}, {"role": "assistant", "content": "answer 3"}]
    assert json.loads(bridge.chat("new"))["session_id"].startswith("mcp-")


@pytest.mark.parametrize("outcome, says", [
    (ConnectionError("refused"), "not running at"),
    (TimeoutError("slow"), "did not answer within"),
    (Reply(500, ValueError("Expecting value: line 1 column 1 (char 0)")), "not as an AI Brainstem"),
    (Reply(400, {"error": "user_input is required"}), "refused the request (HTTP 400): user_input is required"),
    (Reply(200, {"error": "model unavailable"}), "refused the request (HTTP 200): model unavailable"),
    (Reply(200, {"model": "m"}), "without an answer"),
])
def test_failures_are_tool_errors_in_plain_words_and_not_remembered(bridge, monkeypatch, outcome, says):
    requests = bridge.requests
    if isinstance(outcome, ConnectionError):
        outcome = requests.ConnectionError("refused")
    elif isinstance(outcome, TimeoutError):
        outcome = requests.Timeout("slow")
    fail_with(bridge, monkeypatch, outcome)
    with pytest.raises(bridge.ToolError) as caught:
        bridge.chat("hello", session_id="s2")
    assert says in str(caught.value) and "Expecting value" not in str(caught.value)
    assert "s2" not in bridge._histories


def test_capabilities_failure_is_a_tool_error(bridge, monkeypatch):
    fail_with(bridge, monkeypatch, bridge.requests.ConnectionError("refused"))
    with pytest.raises(bridge.ToolError, match="not running at"):
        bridge.capabilities()


def test_capabilities_reports_only_health_facts(bridge):
    assert json.loads(bridge.capabilities()) == {
        "status": "ok", "version": "0.6.16", "model": "m", "agents": ["Twins"], "quarantined": []}
