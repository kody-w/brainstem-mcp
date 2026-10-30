from agents.basic_agent import BasicAgent


def _agent(name, description="d"):
    return {"name": name, "description": description, "parameters": {"type": "object", "properties": {}, "required": []}}


class Alpha(BasicAgent):
    def __init__(self):
        super().__init__(name="Alpha", metadata=_agent("Alpha", "First."))

    def perform(self, **kwargs):
        return {"answer": 42}


class _Hidden(BasicAgent):
    def __init__(self):
        super().__init__(name="Hidden", metadata=_agent("Hidden"))

    def perform(self, **kwargs):
        return "never served"


class BadName(BasicAgent):
    def __init__(self):
        super().__init__(name="bad name!", metadata=_agent("bad name!"))

    def perform(self, **kwargs):
        return "never served"


class TwinOne(BasicAgent):
    def __init__(self):
        super().__init__(name="Twin", metadata=_agent("Twin"))

    def perform(self, **kwargs):
        return "one"


class TwinTwo(BasicAgent):
    def __init__(self):
        super().__init__(name="Twin", metadata=_agent("Twin"))

    def perform(self, **kwargs):
        return "two"


class Broken(BasicAgent):
    def __init__(self):
        super().__init__(name="Broken", metadata=_agent("Broken", "Raises."))

    def perform(self, **kwargs):
        raise ValueError("the agent gave up")
