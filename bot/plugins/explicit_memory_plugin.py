from typing import Dict

from .plugin import Plugin


class ExplicitMemoryPlugin(Plugin):
    """Handles explicit 'remember this' and 'forget this' requests.
    These write directly to the store (not queued) for immediate effect."""

    def __init__(self, store, ollama):
        self.store = store
        self.ollama = ollama

    def get_source_name(self) -> str:
        return "Memory"

    def get_spec(self) -> [Dict]:
        return [
            {
                "name": "remember_fact",
                "description": (
                    "Remember a specific fact about the user when they explicitly ask "
                    "you to remember something. Also use this when a user introduces "
                    "themselves or asks to be called a specific name. "
                    "Only use when the user clearly wants something remembered — "
                    "do NOT use this proactively. Background extraction handles that. "
                    "The user_id and chat_id are provided automatically."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "A concise fact to remember"
                        }
                    },
                    "required": ["fact"]
                }
            },
            {
                "name": "forget_fact",
                "description": (
                    "Remove a previously stored fact. Use when a user asks you to "
                    "forget something, or when you notice a fact in context that "
                    "is clearly outdated. The user_id is provided automatically."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "The fact (or part of it) to remove"
                        }
                    },
                    "required": ["fact"]
                }
            }
        ]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        user_id = kwargs.get('_user_id')
        chat_id = kwargs.get('_chat_id')
        if user_id is None:
            return {"error": "No user context available"}
        user_id = int(user_id)

        if function_name == "remember_fact":
            fact = kwargs["fact"]
            embedding = await self.ollama.embed(fact)
            if embedding is None:
                return {"error": "Embedding service unavailable"}

            # Determine scope
            is_dm = (chat_id is None or chat_id == user_id)
            scope = 'dm' if is_dm else 'group'
            origin = chat_id if chat_id else user_id

            await self.store.add_fact(
                user_id=user_id,
                origin_chat_id=origin,
                fact=fact,
                embedding=embedding,
                source='explicit',
                scope=scope,
            )
            return {"result": f"Remembered: {fact}"}

        elif function_name == "forget_fact":
            # Search for matching facts by embedding similarity
            fact = kwargs["fact"]
            embedding = await self.ollama.embed(fact)
            if embedding is None:
                return {"error": "Embedding service unavailable"}

            # Find the closest matching active fact
            results = await self.store.search_facts(
                user_id=user_id,
                query_embedding=embedding,
                chat_id=chat_id or user_id,
                is_dm=True,  # search all facts for deletion
                top_n=1,
                threshold=0.5,
            )
            if results:
                await self.store.remove_fact_by_id(results[0]['id'])
                return {"result": f"Removed: {results[0]['fact']}"}
            return {"result": f"No matching fact found for: {fact}"}

        return {"error": f"Unknown function: {function_name}"}
