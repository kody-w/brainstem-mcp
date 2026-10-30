import os
import sys
import time

from agents.basic_agent import BasicAgent


def _meta(name):
    return {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}


class Crash(BasicAgent):
    def __init__(self):
        super().__init__(name="Crash", metadata=_meta("Crash"))

    def perform(self, **kwargs):
        os._exit(9)


class Hang(BasicAgent):
    def __init__(self):
        super().__init__(name="Hang", metadata=_meta("Hang"))

    def perform(self, **kwargs):
        time.sleep(120)
        return "too late"


class Chatty(BasicAgent):
    def __init__(self):
        super().__init__(name="Chatty", metadata=_meta("Chatty"))

    def perform(self, **kwargs):
        print('{"jsonrpc": "2.0", "method": "noise"}')
        os.write(1, b"raw noise on fd 1\n")
        sys.stdout.flush()
        return "clean answer"


class Fine(BasicAgent):
    def __init__(self):
        super().__init__(name="Fine", metadata=_meta("Fine"))

    def perform(self, **kwargs):
        return "still here"


class Spawner(BasicAgent):
    """Starts a helper that outlives the call, then answers at once."""

    def __init__(self):
        super().__init__(name="Spawner", metadata=_meta("Spawner"))

    def perform(self, **kwargs):
        import subprocess

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
        with open(os.environ["ROUGH_PID_FILE"], "w") as f:
            f.write(str(child.pid))
        return "started"


class Sleepy(BasicAgent):
    """Writes its worker's pid, then sleeps long past any test."""

    def __init__(self):
        super().__init__(name="Sleepy", metadata=_meta("Sleepy"))

    def perform(self, **kwargs):
        with open(os.environ["ROUGH_PID_FILE"], "w") as f:
            f.write(str(os.getpid()))
        time.sleep(60)
        return "too late"
