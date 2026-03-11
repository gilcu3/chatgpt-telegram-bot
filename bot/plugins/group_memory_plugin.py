from typing import Dict

from .plugin import Plugin


class GroupMemoryPlugin(Plugin):
    """
    A plugin that allows Claude to remember shared facts about groups.
    The bot injects the current chat_id into the helper before calling Claude,
    and this plugin reads it to store facts against the right group.
    """

    def __init__(self, group_memory):
        self.group_memory = group_memory

    def get_source_name(self) -> str:
        return "Group Memory"

    def get_spec(self) -> [Dict]:
        return [
            {
                "name": "remember_group_fact",
                "description": "Remember a shared fact about the current group chat. Use this to store "
                               "group decisions, project context, recurring topics, or any information "
                               "that is relevant to the group as a whole (not to a specific individual). "
                               "Examples: 'The team decided to use PostgreSQL', 'Weekly standup is Monday 10am'. "
                               "The chat_id is provided automatically — you only need to supply the fact.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "A concise group-relevant fact to remember"
                        }
                    },
                    "required": ["fact"]
                }
            },
            {
                "name": "forget_group_fact",
                "description": "Remove a previously stored group fact. Use this to correct outdated, "
                               "wrong, or contradictory group information. Matches by substring so you "
                               "don't need to quote the fact exactly. The chat_id is provided automatically.",
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
        chat_id = kwargs.get('_chat_id')
        if chat_id is None:
            return {"error": "No group context available"}
        chat_id = int(chat_id)

        if function_name == "remember_group_fact":
            fact = kwargs["fact"]
            self.group_memory.add_note(chat_id, fact)
            return {"result": f"Remembered group fact: {fact}"}

        elif function_name == "forget_group_fact":
            fact = kwargs["fact"]
            removed = self.group_memory.remove_note(chat_id, fact)
            if removed:
                return {"result": f"Removed group fact: {removed}"}
            return {"result": f"No matching group fact found for: {fact}"}

        return {"error": f"Unknown function: {function_name}"}
