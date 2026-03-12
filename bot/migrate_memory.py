"""
Run once: python bot/migrate_memory.py

Reads user_memory/memories.json and group_memory/memories.json,
embeds each fact via Ollama, and inserts into the new SQLite store.
"""

import asyncio
import json
import os
import sys

# Ensure bot/ is on path for local imports
sys.path.insert(0, os.path.dirname(__file__))

from memory_store import MemoryStore
from ollama_client import OllamaClient


async def migrate():
    store = MemoryStore('memory/memory.db')
    await store.init()
    ollama = OllamaClient(
        base_url=os.environ.get('OLLAMA_BASE_URL', 'http://frigate.local:11434'),
        chat_model=os.environ.get('OLLAMA_CHAT_MODEL', 'qwen3.5:9b'),
        embed_model=os.environ.get('OLLAMA_EMBED_MODEL', 'nomic-embed-text'),
    )

    # Migrate user memories
    try:
        with open('user_memory/memories.json', 'r') as f:
            user_data = json.load(f)
        for uid, data in user_data.items():
            user_id = int(uid)
            if data.get('name'):
                await store.set_display_name(user_id, data['name'])
            for note in data.get('notes', []):
                embedding = await ollama.embed(note)
                if embedding is not None:
                    await store.add_fact(
                        user_id=user_id,
                        origin_chat_id=user_id,  # treat as DM origin
                        fact=note,
                        embedding=embedding,
                        source='explicit',
                        scope='dm',
                    )
        print(f"Migrated {len(user_data)} users")
    except FileNotFoundError:
        print("No user memories to migrate")

    # Migrate group memories
    try:
        with open('group_memory/memories.json', 'r') as f:
            group_data = json.load(f)
        for cid, data in group_data.items():
            chat_id = int(cid)
            if data.get('persona'):
                await store.set_group_persona(chat_id, data['persona'])
            for note in data.get('notes', []):
                embedding = await ollama.embed(note)
                if embedding is not None:
                    # Group facts: user_id=0 as a sentinel for group-level facts
                    await store.add_fact(
                        user_id=0,
                        origin_chat_id=chat_id,
                        fact=note,
                        embedding=embedding,
                        source='explicit',
                        scope='group',
                    )
        print(f"Migrated {len(group_data)} groups")
    except FileNotFoundError:
        print("No group memories to migrate")

    await ollama.close()
    await store.close()


if __name__ == '__main__':
    asyncio.run(migrate())
