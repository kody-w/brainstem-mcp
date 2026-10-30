"""Echo: says back what you send. The smallest useful agent.py."""
from agents.basic_agent import BasicAgent


class EchoAgent(BasicAgent):
    def __init__(self):
        self.name = "Echo"
        self.metadata = {
            "name": self.name,
            "description": "Say the given text back, optionally in capitals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "What to say back."},
                    "shout": {"type": "boolean", "description": "Say it in capitals."},
                },
                "required": ["text"],
            },
        }
        super().__init__(name=self.name, metadata=self.metadata)

    def perform(self, text="", shout=False, **kwargs):
        return text.upper() if shout else text

    def system_context(self):
        return "Echo says text back. Use it to check that tools work."
