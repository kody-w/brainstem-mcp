"""metadata['name'] differs from self.name. The kernel's to_tool() uses self.name, so that wins."""
from agents.basic_agent import BasicAgent


class GreeterAgent(BasicAgent):
    def __init__(self):
        self.name = "Greeter"
        self.metadata = {
            "name": "greeter_meta_name",
            "description": "Greet someone, in any of three ways.",
            "parameters": {
                "type": "object",
                "properties": {
                    "who": {"type": "string", "description": "Who to greet."},
                    "excited": {"type": "boolean", "description": "Optional: end with an exclamation mark."},
                    "times": {"type": "integer", "description": "Optional: how many times."},
                    "style": {"type": "string", "enum": ["plain", "formal"], "description": "Optional."},
                },
                "required": ["who"],
            },
        }
        super().__init__(name=self.name, metadata=self.metadata)

    def perform(self, who="", excited=False, times=1, style="plain", **kwargs):
        word = "Good day" if style == "formal" else "Hi"
        return " ".join([f"{word} {who}{'!' if excited else '.'}"] * int(times))
