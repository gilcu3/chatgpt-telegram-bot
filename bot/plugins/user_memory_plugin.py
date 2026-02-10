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
                               "conversations so you can greet them by name in the future.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user ID from the message prefix (provided in the system context)"
                        },
                        "name": {
                            "type": "string",
                            "description": "The name the user wants to be called"
                        }
                    },
                    "required": ["user_id", "name"]
                }
            },
            {
                "name": "remember_user_fact",
                "description": "Remember a notable fact about a user for future reference. Use this "
                               "when a user shares something personally significant — their job, "
                               "hobbies, preferences, or important life details. Don't store trivial "
                               "or conversational things. This persists across conversations.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The user ID from the message prefix (provided in the system context)"
                        },
                        "fact": {
                            "type": "string",
                            "description": "A concise fact about the user to remember"
                        }
                    },
                    "required": ["user_id", "fact"]
                }
            }
        ]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        user_id = int(kwargs["user_id"])

        if function_name == "remember_user_name":
            name = kwargs["name"]
            self.user_memory.set_display_name(user_id, name)
            return {"result": f"Remembered: user's preferred name is {name}"}

        elif function_name == "remember_user_fact":
            fact = kwargs["fact"]
            self.user_memory.add_note(user_id, fact)
            return {"result": f"Remembered: {fact}"}

        return {"error": f"Unknown function: {function_name}"}
