import fcntl
import json
import logging
import os
import pathlib

MAX_NOTES_PER_GROUP = 30


class GroupMemory:
    """
    Persistent per-group memory store. Saves shared facts about groups (persona,
    decisions, recurring topics) to a JSON file so the bot can recall them across
    conversations.
    """

    def __init__(self, memory_dir="group_memory"):
        self.memory_dir = memory_dir
        self.memory_file = os.path.join(memory_dir, "memories.json")
        self.memories = {}  # {str(chat_id): {"persona": ..., "name": ..., "notes": [...]}}

        if os.path.isfile(self.memory_file):
            with open(self.memory_file, "r", encoding="utf-8") as f:
                try:
                    self.memories = json.load(f)
                except json.JSONDecodeError:
                    logging.warning("Corrupted group memory file, starting fresh")
                    self.memories = {}
        else:
            pathlib.Path(memory_dir).mkdir(exist_ok=True)

    def _save(self):
        pathlib.Path(self.memory_dir).mkdir(exist_ok=True)
        with open(self.memory_file, "w", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(self.memories, f, indent=2, ensure_ascii=False)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _ensure_group(self, chat_id: int) -> dict:
        cid = str(chat_id)
        if cid not in self.memories:
            self.memories[cid] = {"persona": None, "name": None, "notes": []}
        return self.memories[cid]

    def get_group(self, chat_id: int) -> dict:
        return self.memories.get(str(chat_id), {})

    def get_persona(self, chat_id: int) -> str | None:
        return self.get_group(chat_id).get("persona")

    def set_persona(self, chat_id: int, persona_text: str):
        group = self._ensure_group(chat_id)
        group["persona"] = persona_text
        self._save()

    def clear_persona(self, chat_id: int):
        group = self.get_group(chat_id)
        if group:
            group["persona"] = None
            self._save()

    def get_group_name(self, chat_id: int) -> str | None:
        return self.get_group(chat_id).get("name")

    def set_group_name(self, chat_id: int, name: str):
        group = self._ensure_group(chat_id)
        group["name"] = name
        self._save()

    def add_note(self, chat_id: int, note: str) -> bool:
        group = self._ensure_group(chat_id)
        notes = group["notes"]
        if note in notes:
            return True
        if len(notes) >= MAX_NOTES_PER_GROUP:
            notes.pop(0)
        notes.append(note)
        self._save()
        return True

    def get_notes(self, chat_id: int) -> list[str]:
        return self.get_group(chat_id).get("notes", [])

    def remove_note(self, chat_id: int, fact: str) -> str | None:
        cid = str(chat_id)
        if cid not in self.memories:
            return None
        notes = self.memories[cid]["notes"]
        fact_lower = fact.lower()
        for i, note in enumerate(notes):
            if fact_lower in note.lower() or note.lower() in fact_lower:
                removed = notes.pop(i)
                self._save()
                return removed
        return None

    def get_context_string(self, chat_id: int) -> str | None:
        group = self.get_group(chat_id)
        if not group:
            return None
        parts = []
        if group.get("name"):
            parts.append(f"Group name: {group['name']}")
        if group.get("persona"):
            parts.append(f"Persona: {group['persona']}")
        if group.get("notes"):
            parts.append("Group facts: " + "; ".join(group["notes"]))
        if not parts:
            return None
        return "[Group memory: " + ", ".join(parts) + "]"

    def clear_group(self, chat_id: int):
        cid = str(chat_id)
        if cid in self.memories:
            del self.memories[cid]
            self._save()

    def get_all_formatted(self, chat_id: int) -> str:
        group = self.get_group(chat_id)
        if not group:
            return "No group memories stored."
        lines = []
        if group.get("persona"):
            lines.append(f"Persona: {group['persona']}")
        if group.get("name"):
            lines.append(f"Group name: {group['name']}")
        if group.get("notes"):
            for i, note in enumerate(group["notes"], 1):
                lines.append(f"{i}. {note}")
        return "\n".join(lines) if lines else "No group memories stored."
