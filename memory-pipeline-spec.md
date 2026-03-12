# Async Memory Pipeline — Implementation Spec

## Overview

This document specifies a complete replacement for the existing memory system in the Claude Telegram Bot (`Alpha162/chatgpt-telegram-bot`, `develop` branch). The goal is to move from synchronous, tool-call-driven memory to an async pipeline backed by SQLite and a local LLM (Ollama), with embedding-based relevance filtering and privacy-scoped retrieval.

**Key outcomes:**
- Streaming responses restored (no more tool-call fallback to non-streaming)
- Background fact extraction via local LLM (no Claude API cost for memory)
- Embedding-based relevance filtering (only inject relevant facts, not all of them)
- Privacy-scoped memory (group facts stay in their group; DMs see everything)
- Summarisation offloaded to local LLM

---

## Architecture

### Hot Path (user-facing, synchronous)

```
Telegram message
  → _prefix_with_user_context() embeds the query via Ollama nomic-embed-text
  → SQLite cosine similarity search returns top-N relevant facts
  → Scope enforcer filters: DM = all user facts; Group = only facts from this group
  → Relevant facts injected into prompt
  → Claude API call (streaming enabled, no memory tools needed)
  → Response sent to user
  → Conversation turn queued for cold path
```

### Cold Path (background, async)

```
asyncio.Queue picks up the conversation turn
  → Qwen3.5 via Ollama extracts structured facts
  → Each fact is embedded via nomic-embed-text
  → Facts written to SQLite with full metadata
  → (Periodically) Qwen3.5 consolidates/deduplicates old facts
```

### Failure Mode

If Ollama is unreachable, the bot skips memory entirely — no injection, no extraction. The conversation proceeds without context. Do NOT fall back to dumping all facts into the prompt.

---

## Environment Configuration

Add these to `.env.example` and wire through `main.py` → config dicts:

```env
# Memory pipeline (Ollama)
OLLAMA_BASE_URL=http://frigate.local:11434
OLLAMA_CHAT_MODEL=qwen3.5:9b
OLLAMA_EMBED_MODEL=nomic-embed-text
MEMORY_TOP_N=10
MEMORY_RELEVANCE_THRESHOLD=0.3
MEMORY_MAX_FACTS_PER_USER=200
MEMORY_MAX_FACTS_PER_GROUP=100
MEMORY_DB_PATH=memory/memory.db
```

Add to `requirements.txt`:
```
numpy>=1.26.0
aiosqlite>=0.19.0
httpx>=0.25.0  # already present
```

---

## SQLite Schema

File: `bot/memory_store.py`

Create a new class `MemoryStore` that manages all database operations.

```sql
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('dm', 'group')),
    origin_chat_id INTEGER NOT NULL,
    fact TEXT NOT NULL,
    embedding BLOB NOT NULL,         -- numpy float32 array, stored as bytes
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL CHECK(source IN ('extracted', 'explicit')),
    is_active INTEGER DEFAULT 1       -- soft delete for superseded facts
);

CREATE INDEX IF NOT EXISTS idx_facts_user_scope ON facts(user_id, scope, is_active);
CREATE INDEX IF NOT EXISTS idx_facts_origin ON facts(user_id, origin_chat_id, is_active);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id INTEGER PRIMARY KEY,
    display_name TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS group_profiles (
    chat_id INTEGER PRIMARY KEY,
    group_name TEXT,
    persona TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS summaries (
    chat_id INTEGER PRIMARY KEY,
    summary TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### MemoryStore class interface

```python
class MemoryStore:
    def __init__(self, db_path: str):
        """Initialise DB, create tables if not exist."""

    async def init(self):
        """Async init — call once at startup. Creates connection and tables."""

    async def add_fact(self, user_id: int, origin_chat_id: int, fact: str,
                       embedding: np.ndarray, source: str = 'extracted',
                       scope: str = 'dm') -> int:
        """Insert a fact. Returns the fact ID. Checks for near-duplicates
        (cosine similarity > 0.92 against existing active facts for this user)
        and skips if duplicate found. If the new fact supersedes an old one
        (similarity 0.75-0.92), soft-delete the old fact before inserting."""

    async def search_facts(self, user_id: int, query_embedding: np.ndarray,
                           chat_id: int, is_dm: bool,
                           top_n: int = 10, threshold: float = 0.3) -> list[dict]:
        """Retrieve top-N relevant active facts for a user.
        Scope logic:
          - If is_dm=True: return facts from ALL origin_chat_ids for this user
          - If is_dm=False: return ONLY facts where origin_chat_id == chat_id
        Returns list of dicts: {id, fact, score, created_at}
        sorted by descending cosine similarity, filtered by threshold."""

    async def get_display_name(self, user_id: int) -> str | None:
        """Get user's preferred display name."""

    async def set_display_name(self, user_id: int, name: str):
        """Set user's preferred display name."""

    async def get_user_facts_formatted(self, user_id: int) -> str:
        """Return all active facts for a user, formatted for /mymemory command."""

    async def clear_user(self, user_id: int):
        """Delete all facts and profile for a user (/forgetme)."""

    async def remove_fact_by_id(self, fact_id: int):
        """Soft-delete a specific fact."""

    async def get_group_persona(self, chat_id: int) -> str | None:
        """Get group persona."""

    async def set_group_persona(self, chat_id: int, persona: str):
        """Set group persona."""

    async def get_group_facts_formatted(self, chat_id: int) -> str:
        """Return all active group facts, formatted for display."""

    async def clear_group(self, chat_id: int):
        """Delete all facts and profile for a group."""

    async def store_summary(self, chat_id: int, summary: str):
        """Store/update a conversation summary."""

    async def get_summary(self, chat_id: int) -> str | None:
        """Retrieve the last conversation summary."""

    async def close(self):
        """Close the database connection."""
```

### Cosine similarity in Python

Since SQLite doesn't have vector operations, load all active facts for the user into memory and compute cosine similarity using numpy. With a cap of 200 facts per user, this is negligible — well under 1ms.

```python
import numpy as np

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))
```

Store embeddings as `embedding.tobytes()` in the BLOB column, load with `np.frombuffer(blob, dtype=np.float32)`.

---

## Ollama Client

File: `bot/ollama_client.py`

A thin async wrapper around Ollama's HTTP API.

```python
class OllamaClient:
    def __init__(self, base_url: str, chat_model: str, embed_model: str):
        """Configure the client. Uses httpx.AsyncClient internally."""

    async def embed(self, text: str) -> np.ndarray | None:
        """POST /api/embed with model=embed_model.
        Returns numpy float32 array, or None if Ollama is unreachable.
        Timeout: 10 seconds."""

    async def extract_facts(self, conversation_text: str, user_name: str) -> list[str]:
        """POST /api/chat with model=chat_model.
        Sends the extraction prompt (see below) with the conversation text.
        Parses the JSON response into a list of fact strings.
        Returns empty list if Ollama is unreachable or response is invalid.
        Timeout: 30 seconds."""

    async def summarise(self, conversation_text: str) -> str | None:
        """POST /api/chat with model=chat_model.
        Sends the summarisation prompt (see below).
        Returns the summary string, or None if Ollama is unreachable.
        Timeout: 30 seconds."""

    async def is_available(self) -> bool:
        """GET /api/tags — returns True if Ollama responds within 3 seconds."""

    async def close(self):
        """Close the httpx client."""
```

### Extraction Prompt

This is sent to Qwen3.5 via Ollama for each conversation turn:

```
You are a fact extraction system. Analyse the following conversation between a user and an AI assistant.

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
{conversation_text}
```

### Summarisation Prompt

```
Summarise the following conversation concisely. Capture the key topics discussed, any decisions made, and the current state of any ongoing tasks. Keep it under 500 characters.

Conversation:
{conversation_text}
```

---

## Memory Extraction Worker

File: `bot/memory_worker.py`

An async background worker that processes conversation turns from a queue.

```python
class MemoryWorker:
    def __init__(self, ollama: OllamaClient, store: MemoryStore, config: dict):
        self.ollama = ollama
        self.store = store
        self.queue = asyncio.Queue(maxsize=100)
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
```

---

## Integration Points — Changes to Existing Files

### `bot/main.py`

1. Import new modules: `MemoryStore`, `OllamaClient`, `MemoryWorker`
2. Read Ollama config from environment
3. Create `MemoryStore` instance and call `await store.init()` (will need to be handled in an async context — either wrap in `asyncio.run()` during setup or defer to `post_init`)
4. Create `OllamaClient` instance
5. Create `MemoryWorker` instance
6. Pass all three to `ClaudeHelper` and `ChatGPTTelegramBot`
7. Remove the old `UserMemory()` and `GroupMemory()` instantiation
8. Remove `user_memory` and `group_memory` from `PluginManager` constructor — the memory plugins are no longer needed

### `bot/plugin_manager.py`

1. Remove `UserMemoryPlugin` and `GroupMemoryPlugin` imports
2. Remove the auto-registration of these plugins (lines 51-57)
3. Keep a single lightweight `ExplicitMemoryPlugin` (see below) that handles "remember this" requests

### `bot/plugins/explicit_memory_plugin.py` (NEW)

Replace `user_memory_plugin.py` and `group_memory_plugin.py` with a single plugin:

```python
class ExplicitMemoryPlugin(Plugin):
    """Handles explicit 'remember this' and 'forget this' requests.
    These write directly to the store (not queued) for immediate effect."""

    def __init__(self, store: MemoryStore, ollama: OllamaClient):
        self.store = store
        self.ollama = ollama

    def get_source_name(self) -> str:
        return "Memory"

    def get_spec(self) -> list[dict]:
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

    async def execute(self, function_name, helper, **kwargs) -> dict:
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
```

### `bot/claude_helper.py`

#### Constructor changes

Add `ollama_client`, `memory_store`, and `memory_worker` parameters. Remove `group_memory`.

#### `_build_system_prompt()` changes

Strip out the aggressive memory tool instructions. Replace with a lightweight instruction:

```python
memory_supplement = (
    "\n\nYou have access to a persistent memory system. Relevant facts about the user "
    "may appear in brackets before their message — use them naturally without drawing "
    "attention to the fact that you're reading from stored memory."
    "\n- You have a remember_fact tool. ONLY use it when a user explicitly asks you to "
    "remember something (e.g. 'remember that I...', 'my name is...'). Do NOT call it "
    "proactively — background extraction handles that automatically."
    "\n- You have a forget_fact tool for when users ask you to forget something."
    "\n- In group chats, messages are prefixed with the sender's name."
    "\n- Each message includes a timestamp in [YYYY-MM-DD HH:MM TZ] format."
    "\n- You also have scheduling tools (set_reminder, set_recurring_schedule, "
    "list_reminders, cancel_reminder) for managing reminders."
)
```

This dramatically reduces the system prompt token overhead AND reduces unnecessary tool invocations.

#### `__prepare_chat()` changes

Remove the group memory context injection block (lines 327-337). Memory context is now handled in `_prefix_with_user_context()` on the telegram_bot side.

#### `__summarise()` changes

Replace the Claude API call with a call to the Ollama client:

```python
async def __summarise(self, conversation) -> str:
    """Summarise conversation history using local LLM."""
    summary = await self.ollama.summarise(str(conversation))
    if summary:
        return summary
    # Fallback: simple truncation if Ollama is down
    logging.warning("Ollama unavailable for summarisation, truncating history")
    return "Previous conversation context unavailable."
```

#### Streaming restoration

The streaming path at line 220-224 currently checks `if has_tools:` and forces non-streaming. With the explicit memory plugin still registered, tools will still exist, so this fallback remains. However, because the system prompt now tells Claude to only use the tool when explicitly asked, tool invocations will be rare — the vast majority of messages will flow through the standard streaming path in practice.

**Optional optimisation**: Split the tool decision — if the user's message doesn't contain trigger phrases like "remember", "forget", "my name is", "call me", don't pass tools to the API call at all for that request, allowing pure streaming. This is an optional enhancement, not required for the initial implementation.

### `bot/telegram_bot.py`

#### Constructor changes

Replace `user_memory: UserMemory` and `group_memory: GroupMemory` params with `memory_store: MemoryStore`, `ollama: OllamaClient`, `memory_worker: MemoryWorker`.

#### `_prefix_with_user_context()` rewrite

This is the core hot-path change:

```python
async def _prefix_with_user_context(self, update: Update, prompt: str) -> str:
    """
    Prefixes a prompt with sender identity and relevant memory context.
    Uses embedding search with scope enforcement.
    """
    user_id = update.message.from_user.id
    first_name = update.message.from_user.first_name
    display_name = await self.memory_store.get_display_name(user_id) or first_name
    is_dm = not is_group_chat(update)
    chat_id = update.effective_chat.id

    # Embed the user's message for relevance search
    query_embedding = await self.ollama.embed(prompt)

    memory_context = None
    if query_embedding is not None:
        results = await self.memory_store.search_facts(
            user_id=user_id,
            query_embedding=query_embedding,
            chat_id=chat_id,
            is_dm=is_dm,
            top_n=self.config.get('memory_top_n', 10),
            threshold=self.config.get('memory_relevance_threshold', 0.3),
        )
        if results:
            facts = "; ".join(r['fact'] for r in results)
            memory_context = f"[User memory: Preferred name: {display_name}, Known facts: {facts}]"
        elif display_name != first_name:
            memory_context = f"[User memory: Preferred name: {display_name}]"

    # Also include group persona/context if in a group
    if not is_dm:
        persona = await self.memory_store.get_group_persona(chat_id)
        # Group persona is handled via system prompt in claude_helper

    if is_group_chat(update):
        prefix = f"{display_name}: "
        if memory_context:
            prefix = f"{memory_context} {prefix}"
        return f"{prefix}{prompt}"
    else:
        if memory_context:
            return f"{memory_context}\n{prompt}"
        return prompt
```

**IMPORTANT**: This method is now `async`. All call sites must be updated to `await` it.

#### Post-response queueing

After each successful response in the message handler, queue the turn for extraction:

```python
# After response is sent to user
conversation_text = f"{display_name}: {original_prompt}\nAssistant: {response_text}"
await self.memory_worker.enqueue(
    user_id=user_id,
    chat_id=update.effective_chat.id,
    is_dm=not is_group_chat(update),
    user_name=display_name,
    conversation_text=conversation_text,
)
```

#### `/mymemory` command

Update to use `self.memory_store.get_user_facts_formatted(user_id)` (now async).

#### `/forgetme` command

Update to use `self.memory_store.clear_user(user_id)` (now async).

#### `post_init()` hook

Start the memory worker:
```python
self.memory_worker.start()
```

---

## Data Migration

File: `bot/migrate_memory.py`

A one-time script to migrate existing JSON memories to SQLite.

```python
"""
Run once: python bot/migrate_memory.py

Reads user_memory/memories.json and group_memory/memories.json,
embeds each fact via Ollama, and inserts into the new SQLite store.
"""

import asyncio
import json
from memory_store import MemoryStore
from ollama_client import OllamaClient

async def migrate():
    store = MemoryStore('memory/memory.db')
    await store.init()
    ollama = OllamaClient(
        base_url='http://frigate.local:11434',
        chat_model='qwen3.5:9b',
        embed_model='nomic-embed-text'
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

asyncio.run(migrate())
```

---

## Files to Delete

Once the new system is working and migration is confirmed:

- `bot/user_memory.py` — replaced by `memory_store.py`
- `bot/group_memory.py` — replaced by `memory_store.py`
- `bot/plugins/user_memory_plugin.py` — replaced by `explicit_memory_plugin.py`
- `bot/plugins/group_memory_plugin.py` — merged into `explicit_memory_plugin.py`
- `user_memory/` directory — data migrated to SQLite
- `group_memory/` directory — data migrated to SQLite

---

## New Files Summary

| File | Purpose |
|------|---------|
| `bot/memory_store.py` | SQLite-backed memory store with embedding search |
| `bot/ollama_client.py` | Async HTTP client for Ollama (chat + embed) |
| `bot/memory_worker.py` | Background fact extraction worker |
| `bot/plugins/explicit_memory_plugin.py` | Lightweight tool for explicit remember/forget |
| `bot/migrate_memory.py` | One-time JSON → SQLite migration script |

---

## Testing Checklist

1. **Ollama available**: Send a message, verify response streams, check SQLite for extracted facts after ~10 seconds
2. **Ollama down**: Send a message, verify response still works (no memory context), no crash, log warning
3. **DM privacy**: Tell the bot a fact in DM, then check it's available in DM but NOT in a group chat
4. **Group isolation**: Tell the bot a fact in Group A, verify it's NOT available in Group B
5. **Explicit remember**: Say "remember that I love sushi", verify it's stored immediately (not queued)
6. **Explicit forget**: Say "forget that I love sushi", verify the fact is soft-deleted
7. **Relevance**: Store 20+ facts, send a message about cooking, verify only food-related facts are injected
8. **Deduplication**: Mention the same fact twice in different conversations, verify only one copy in DB
9. **Superseding**: Say "I work at Aptum", then later "I work at Google", verify old fact is soft-deleted
10. **/mymemory**: Verify it shows all active facts for the user
11. **/forgetme**: Verify it clears everything
12. **Migration**: Run migrate script, verify old JSON facts appear in SQLite with embeddings
13. **Summarisation**: Let a conversation exceed MAX_HISTORY_SIZE, verify summary is generated by Ollama not Claude

---

## Implementation Order

1. `bot/ollama_client.py` — can test independently against Ollama
2. `bot/memory_store.py` — can test independently with SQLite
3. `bot/memory_worker.py` — depends on 1 and 2
4. `bot/plugins/explicit_memory_plugin.py` — depends on 1 and 2
5. Wire into `bot/main.py` — instantiation and config
6. Update `bot/telegram_bot.py` — hot path changes, async prefix, post-response queueing
7. Update `bot/claude_helper.py` — system prompt, summarisation, group memory removal
8. Update `bot/plugin_manager.py` — swap plugins
9. `bot/migrate_memory.py` — run against existing data
10. Test end-to-end
11. Delete old files
