from __future__ import annotations

import asyncio
import logging

from ollama_client import OllamaClient
from memory_store import MemoryStore


class MemoryWorker:
    """Background async worker that processes conversation turns for fact extraction."""

    def __init__(self, ollama: OllamaClient, store: MemoryStore, config: dict):
        self.ollama = ollama
        self.store = store
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.config = config
        self._task: asyncio.Task | None = None

    def start(self):
        """Start the background worker as an asyncio task."""
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        """Main loop — pull items from queue, extract and store facts."""
        while True:
            try:
                item = await self.queue.get()
                await self._process(item)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Memory worker error: {e}")

    async def enqueue(self, user_id: int, chat_id: int, is_dm: bool,
                      user_name: str, conversation_text: str):
        """Add a conversation turn to the extraction queue.
        Non-blocking — if queue is full, drop the item and log a warning."""
        item = {
            'user_id': user_id,
            'chat_id': chat_id,
            'is_dm': is_dm,
            'user_name': user_name,
            'conversation_text': conversation_text,
        }
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            logging.warning("Memory extraction queue full, dropping item")

    async def _process(self, item: dict):
        """Extract facts from a conversation turn and store them."""
        facts = await self.ollama.extract_facts(
            item['conversation_text'],
            item['user_name']
        )
        if not facts:
            return

        scope = 'dm' if item['is_dm'] else 'group'

        for fact_text in facts:
            embedding = await self.ollama.embed(fact_text)
            if embedding is None:
                continue
            await self.store.add_fact(
                user_id=item['user_id'],
                origin_chat_id=item['chat_id'],
                fact=fact_text,
                embedding=embedding,
                source='extracted',
                scope=scope,
            )

    async def stop(self):
        """Cancel the background task."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
