from agents.basic_agent import BasicAgent


class AlphaAgain(BasicAgent):
    def __init__(self):
        super().__init__(name="Alpha", metadata={"name": "Alpha", "description": "Later copy."})

    def perform(self, **kwargs):
        return "later copy"


class Beta(BasicAgent):
    def __init__(self):
        super().__init__(name="Beta", metadata={"name": "Beta", "description": "No parameters key."})

    def perform(self, **kwargs):
        return "beta"
