"""Agents that say they only read, and one that returns JSON."""
import json

from agents.basic_agent import BasicAgent


class Lookup(BasicAgent):
    """Declares read_only in its metadata, and answers in JSON."""

    def __init__(self):
        super().__init__(name="Lookup", metadata={
            "name": "Lookup",
            "description": "Look up the weather for a city.",
            "read_only": True,
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        })

    def perform(self, city="Paris", **kwargs):
        return json.dumps({"city": city, "temp": 21})


class Peek(BasicAgent):
    """Declares read_only as a class attribute."""

    read_only = True

    def __init__(self):
        super().__init__(name="Peek", metadata={"name": "Peek", "description": "Peek at a value."})

    def perform(self, **kwargs):
        return "a value"


class Plain(BasicAgent):
    """Says nothing about reading or writing, so it gets no hints."""

    def __init__(self):
        super().__init__(name="Plain", metadata={"name": "Plain", "description": "Change a thing."})

    def perform(self, **kwargs):
        return "changed"
