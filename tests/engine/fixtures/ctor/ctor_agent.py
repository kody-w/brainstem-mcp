"""A constructor that throws stops the rest of its file, as the kernel's loader does.

Classes load in dir() order: Aok, then Boom (throws), then Cafter (never reached).
"""
from agents.basic_agent import BasicAgent


def _meta(name):
    return {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}


class Aok(BasicAgent):
    def __init__(self):
        super().__init__(name="Aok", metadata=_meta("Aok"))

    def perform(self, **kwargs):
        return "ok"


class Boom(BasicAgent):
    def __init__(self):
        raise RuntimeError("no")

    def perform(self, **kwargs):
        return "never"


class Cafter(BasicAgent):
    def __init__(self):
        super().__init__(name="Cafter", metadata=_meta("Cafter"))

    def perform(self, **kwargs):
        return "after"
