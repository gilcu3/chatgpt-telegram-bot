import json
import logging
import os
import pathlib

MAX_NOTES_PER_USER = 20


class UserMemory:
    """
    Persistent per-user memory store. Saves facts about users (preferred name,
    interests, etc.) to a JSON file so the bot can recall them across conversations.
    """

    def __init__(self, memory_dir="user_memory"):
        self.memory_dir = memory_dir
        self.memory_file = os.path.join(memory_dir, "memories.json")
        self.memories = {}  # {str(user_id): {"name": ..., "notes": [...]}}

        if os.path.isfile(self.memory_file):
            with open(self.memory_file, "r", encoding="utf-8") as f:
                try:
                    self.memories = json.load(f)
                except json.JSONDecodeError:
                    logging.warning("Corrupted memory file, starting fresh")
                    self.memories = {}
        else:
            pathlib.Path(memory_dir).mkdir(exist_ok=True)

    def _save(self):
        pathlib.Path(self.memory_dir).mkdir(exist_ok=True)
        with open(self.memory_file, "w", encoding="utf-8") as f:
            json.dump(self.memories, f, indent=2, ensure_ascii=False)

    def get_user(self, user_id: int) -> dict:
        """Returns the memory dict for a user, or empty dict if none."""
        return self.memories.get(str(user_id), {})

    def get_display_name(self, user_id: int) -> str | None:
        """Returns the user's preferred name, or None if not set."""
        return self.get_user(user_id).get("name")

    def set_display_name(self, user_id: int, name: str):
        """Sets the user's preferred display name."""
        uid = str(user_id)
        if uid not in self.memories:
            self.memories[uid] = {"name": name, "notes": []}
        else:
            self.memories[uid]["name"] = name
        self._save()

    def add_note(self, user_id: int, note: str) -> bool:
        """
        Adds a note about the user. Returns False if at capacity.
        """
        uid = str(user_id)
        if uid not in self.memories:
            self.memories[uid] = {"name": None, "notes": []}

        notes = self.memories[uid]["notes"]
        # Avoid exact duplicates
        if note in notes:
            return True

        if len(notes) >= MAX_NOTES_PER_USER:
            # Drop the oldest note to make room
            notes.pop(0)

        notes.append(note)
        self._save()
        return True

    def get_notes(self, user_id: int) -> list[str]:
        """Returns the list of notes for a user."""
        return self.get_user(user_id).get("notes", [])

    def get_context_string(self, user_id: int) -> str | None:
        """
        Returns a formatted context string for injection into prompts,
        or None if there's nothing stored for this user.
        """
        user = self.get_user(user_id)
        if not user:
            return None

        parts = []
        if user.get("name"):
            parts.append(f"Preferred name: {user['name']}")
        if user.get("notes"):
            parts.append("Known facts: " + "; ".join(user["notes"]))

        if not parts:
            return None
        return "[User memory: " + ", ".join(parts) + "]"

    def clear_user(self, user_id: int):
        """Removes all stored memory for a user."""
        uid = str(user_id)
        if uid in self.memories:
            del self.memories[uid]
            self._save()

    def get_all_formatted(self, user_id: int) -> str:
        """Returns a human-readable summary of stored memory for a user."""
        user = self.get_user(user_id)
        if not user:
            return "No memories stored."

        lines = []
        if user.get("name"):
            lines.append(f"Name: {user['name']}")
        if user.get("notes"):
            for i, note in enumerate(user["notes"], 1):
                lines.append(f"{i}. {note}")

        return "\n".join(lines) if lines else "No memories stored."
