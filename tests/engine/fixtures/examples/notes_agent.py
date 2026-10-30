"""Notes: keeps short notes on disk.

Each call runs in a fresh process, so anything kept in memory is gone after
the call. State that must last lives on disk.
"""
import json
import os
import pathlib

from agents.basic_agent import BasicAgent


def _store():
    folder = pathlib.Path(os.environ.get("NOTES_DATA_DIR", "~/.brainstem-mcp-notes")).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "notes.json"


class NotesAgent(BasicAgent):
    def __init__(self):
        self.name = "Notes"
        self.metadata = {
            "name": self.name,
            "description": "Save a short note, or list the notes saved so far.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "list"], "description": "add or list."},
                    "note": {"type": "string", "description": "The note to save, for add."},
                },
                "required": ["action"],
            },
        }
        super().__init__(name=self.name, metadata=self.metadata)

    def perform(self, action="list", note="", **kwargs):
        path = _store()
        notes = json.loads(path.read_text()) if path.exists() else []
        if action == "add":
            if not note.strip():
                return "Say what the note should be."
            notes.append(note.strip())
            path.write_text(json.dumps(notes, indent=2))
            return f"Saved. {len(notes)} note(s) so far."
        if not notes:
            return "No notes yet."
        return "\n".join(f"{i}. {n}" for i, n in enumerate(notes, 1))
