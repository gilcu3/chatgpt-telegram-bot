from typing import Dict

from .plugin import Plugin


class UserMemoryPlugin(Plugin):
    """
    A plugin that allows Claude to remember facts about users.
    The bot injects the current user_id into the helper before calling Claude,
    and this plugin reads it to store facts against the right user.
    """

    def __init__(self, user_memory):
        self.user_memory = user_memory

    def get_source_name(self) -> str:
        return "User Memory"

    def get_spec(self) -> [Dict]:
        return [
            {
                "name": "remember_user_name",
                "description": "Remember a user's preferred name. Call this when a user introduces "
                               "themselves or asks to be called a specific name. This persists across "
                               "conversations so you can greet them by name in the future. "
                               "The user_id is provided automatically — you only need to supply the name.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The name the user wants to be called"
                        }
                    },
                    "required": ["name"]
                }
            },
            {
                "name": "remember_user_fact",
                "description": "Remember a notable fact about a user for future reference. Use this "
                               "when a user shares something personally significant — their job, "
                               "hobbies, preferences, or important life details. Don't store trivial "
                               "or conversational things. This persists across conversations. "
                               "The user_id is provided automatically — you only need to supply the fact.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "A concise fact about the user to remember"
                        }
                    },
                    "required": ["fact"]
                }
            },
            {
                "name": "forget_user_fact",
                "description": "Remove a previously stored fact about a user. Use this to correct "
                               "outdated, wrong, or contradictory information. Also use it when a "
                               "user asks you to forget something. Matches by substring so you don't "
                               "need to quote the fact exactly. The user_id is provided automatically.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "The fact (or a distinctive part of it) to remove"
                        }
                    },
                    "required": ["fact"]
                }
            }
        ]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        user_id = kwargs.get('_user_id')
        if user_id is None:
            return {"error": "No user context available"}
        user_id = int(user_id)

        if function_name == "remember_user_name":
            name = kwargs["name"]
            self.user_memory.set_display_name(user_id, name)
            return {"result": f"Remembered: user's preferred name is {name}"}

        elif function_name == "remember_user_fact":
            fact = kwargs["fact"]
            self.user_memory.add_note(user_id, fact)
            return {"result": f"Remembered: {fact}"}

        elif function_name == "forget_user_fact":
            fact = kwargs["fact"]
            removed = self.user_memory.remove_note(user_id, fact)
            if removed:
                return {"result": f"Removed: {removed}"}
            return {"result": f"No matching fact found for: {fact}"}

        return {"error": f"Unknown function: {function_name}"}
