from __future__ import annotations

import json
import logging

import httpx
import numpy as np


EXTRACTION_PROMPT = """You are a fact extraction system. Analyse the following conversation between a user and an AI assistant.

Extract ONLY notable, persistent facts about the user that would be worth remembering for future conversations. These include:
- Their name or preferred name
- Job, role, employer
- Location, timezone
- Hobbies, interests, skills
- Preferences (food, tech, style, etc.)
- Important life events or circumstances
- Pets, family details
- Projects they're working on
- Opinions or values they've expressed clearly

Do NOT extract:
- Trivial or transient things (greetings, temporary moods, one-off questions)
- Facts about the AI assistant
- Information that's already general knowledge
- Anything uncertain or speculative

The user's name in the chat is: {user_name}

Respond with a JSON array of concise fact strings. Each fact should be a single sentence, self-contained and understandable without the conversation context. If there are no notable facts, respond with an empty array.

Example response: ["Dave works as a Customer Systems Engineer at Aptum Technologies", "Dave drives a BYD Dolphin EV"]

Conversation:
{conversation_text}"""

SUMMARISATION_PROMPT = """Summarise the following conversation concisely. Capture the key topics discussed, any decisions made, and the current state of any ongoing tasks. Keep it under 500 characters.

Conversation:
{conversation_text}"""


class OllamaClient:
    """Async HTTP client for Ollama (chat + embed)."""

    def __init__(self, base_url: str, chat_model: str, embed_model: str):
        self.base_url = base_url.rstrip('/')
        self.chat_model = chat_model
        self.embed_model = embed_model
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def embed(self, text: str) -> np.ndarray | None:
        """Embed text via Ollama. Returns numpy float32 array, or None if unreachable."""
        try:
            response = await self._client.post(
                f"{self.base_url}/api/embed",
                json={"model": self.embed_model, "input": text},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
            embeddings = data.get("embeddings")
            if embeddings and len(embeddings) > 0:
                return np.array(embeddings[0], dtype=np.float32)
            return None
        except Exception as e:
            logging.warning(f"Ollama embed failed: {e}")
            return None

    async def extract_facts(self, conversation_text: str, user_name: str) -> list[str]:
        """Extract facts from conversation via Ollama chat model."""
        prompt = EXTRACTION_PROMPT.format(
            user_name=user_name,
            conversation_text=conversation_text,
        )
        try:
            response = await self._client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.chat_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "format": "json",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("message", {}).get("content", "")
            # Parse the JSON array from the response
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return [str(f) for f in parsed if f]
            # Handle {"facts": [...]} wrapper
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        return [str(f) for f in v if f]
            return []
        except Exception as e:
            logging.warning(f"Ollama extract_facts failed: {e}")
            return []

    async def summarise(self, conversation_text: str) -> str | None:
        """Summarise conversation via Ollama chat model."""
        prompt = SUMMARISATION_PROMPT.format(conversation_text=conversation_text)
        try:
            response = await self._client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.chat_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("message", {}).get("content")
        except Exception as e:
            logging.warning(f"Ollama summarise failed: {e}")
            return None

    async def is_available(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            response = await self._client.get(
                f"{self.base_url}/api/tags",
                timeout=3.0,
            )
            return response.status_code == 200
        except Exception:
            return False

    async def close(self):
        """Close the httpx client."""
        await self._client.aclose()
