from __future__ import annotations

import logging
import os

import aiosqlite
import numpy as np


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('dm', 'group')),
    origin_chat_id INTEGER NOT NULL,
    fact TEXT NOT NULL,
    embedding BLOB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL CHECK(source IN ('extracted', 'explicit')),
    is_active INTEGER DEFAULT 1
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
"""


class MemoryStore:
    """SQLite-backed memory store with embedding search."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self):
        """Async init — call once at startup. Creates connection and tables."""
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()

    async def add_fact(self, user_id: int, origin_chat_id: int, fact: str,
                       embedding: np.ndarray, source: str = 'extracted',
                       scope: str = 'dm') -> int:
        """Insert a fact with dedup/supersede logic. Returns fact ID or -1 if skipped."""
        # Load existing active facts within the same scope to check duplicates
        async with self._db.execute(
            "SELECT id, fact, embedding FROM facts WHERE user_id = ? AND scope = ? AND origin_chat_id = ? AND is_active = 1",
            (user_id, scope, origin_chat_id)
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            existing_emb = np.frombuffer(row['embedding'], dtype=np.float32)
            sim = cosine_similarity(embedding, existing_emb)
            if sim > 0.92:
                # Near-duplicate — skip
                logging.debug(f"Skipping duplicate fact (sim={sim:.3f}): {fact}")
                return -1
            if 0.75 <= sim <= 0.92:
                # Supersedes old fact — soft-delete the old one
                logging.info(f"Superseding fact id={row['id']} (sim={sim:.3f})")
                await self._db.execute(
                    "UPDATE facts SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row['id'],)
                )

        embedding_blob = embedding.tobytes()
        async with self._db.execute(
            """INSERT INTO facts (user_id, scope, origin_chat_id, fact, embedding, source)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, scope, origin_chat_id, fact, embedding_blob, source)
        ) as cursor:
            fact_id = cursor.lastrowid
        await self._db.commit()
        return fact_id

    async def search_facts(self, user_id: int, query_embedding: np.ndarray,
                           chat_id: int, is_dm: bool,
                           top_n: int = 10, threshold: float = 0.3) -> list[dict]:
        """Retrieve top-N relevant active facts with scope enforcement."""
        if is_dm:
            query = "SELECT id, fact, embedding, created_at FROM facts WHERE user_id = ? AND is_active = 1"
            params = (user_id,)
        else:
            query = ("SELECT id, fact, embedding, created_at FROM facts "
                     "WHERE user_id = ? AND origin_chat_id = ? AND is_active = 1")
            params = (user_id, chat_id)

        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

        scored = []
        for row in rows:
            emb = np.frombuffer(row['embedding'], dtype=np.float32)
            score = cosine_similarity(query_embedding, emb)
            if score >= threshold:
                scored.append({
                    'id': row['id'],
                    'fact': row['fact'],
                    'score': score,
                    'created_at': row['created_at'],
                })

        scored.sort(key=lambda x: x['score'], reverse=True)
        return scored[:top_n]

    async def get_display_name(self, user_id: int) -> str | None:
        async with self._db.execute(
            "SELECT display_name FROM user_profiles WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row['display_name'] if row else None

    async def set_display_name(self, user_id: int, name: str):
        await self._db.execute(
            """INSERT INTO user_profiles (user_id, display_name, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET display_name = ?, updated_at = CURRENT_TIMESTAMP""",
            (user_id, name, name)
        )
        await self._db.commit()

    async def get_user_facts_formatted(self, user_id: int) -> str:
        """Return all active facts for a user, formatted for /mymemory command."""
        name = await self.get_display_name(user_id)
        async with self._db.execute(
            "SELECT fact, source, created_at FROM facts WHERE user_id = ? AND is_active = 1 ORDER BY created_at",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows and not name:
            return "No memories stored."

        parts = []
        if name:
            parts.append(f"Name: {name}")
        if rows:
            parts.append("Facts:")
            for i, row in enumerate(rows, 1):
                parts.append(f"  {i}. {row['fact']}")
        return "\n".join(parts)

    async def clear_user(self, user_id: int):
        """Delete all facts and profile for a user."""
        await self._db.execute("DELETE FROM facts WHERE user_id = ?", (user_id,))
        await self._db.execute("DELETE FROM user_profiles WHERE user_id = ?", (user_id,))
        await self._db.commit()

    async def remove_fact_by_id(self, fact_id: int):
        """Soft-delete a specific fact."""
        await self._db.execute(
            "UPDATE facts SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (fact_id,)
        )
        await self._db.commit()

    async def get_group_persona(self, chat_id: int) -> str | None:
        async with self._db.execute(
            "SELECT persona FROM group_profiles WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row['persona'] if row else None

    async def set_group_persona(self, chat_id: int, persona: str):
        await self._db.execute(
            """INSERT INTO group_profiles (chat_id, persona, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(chat_id) DO UPDATE SET persona = ?, updated_at = CURRENT_TIMESTAMP""",
            (chat_id, persona, persona)
        )
        await self._db.commit()

    async def clear_group_persona(self, chat_id: int):
        await self._db.execute(
            "UPDATE group_profiles SET persona = NULL, updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?",
            (chat_id,)
        )
        await self._db.commit()

    async def get_group_facts_formatted(self, chat_id: int) -> str:
        """Return all active group facts, formatted for display."""
        async with self._db.execute(
            "SELECT fact, created_at FROM facts WHERE origin_chat_id = ? AND scope = 'group' AND is_active = 1 ORDER BY created_at",
            (chat_id,)
        ) as cursor:
            rows = await cursor.fetchall()

        persona = await self.get_group_persona(chat_id)
        parts = []
        if persona:
            parts.append(f"Persona: {persona}")
        if rows:
            parts.append("Facts:")
            for i, row in enumerate(rows, 1):
                parts.append(f"  {i}. {row['fact']}")
        if not parts:
            return "No group memories stored."
        return "\n".join(parts)

    async def clear_group(self, chat_id: int):
        """Delete all facts and profile for a group."""
        await self._db.execute(
            "DELETE FROM facts WHERE origin_chat_id = ? AND scope = 'group'", (chat_id,)
        )
        await self._db.execute("DELETE FROM group_profiles WHERE chat_id = ?", (chat_id,))
        await self._db.commit()

    async def store_summary(self, chat_id: int, summary: str):
        await self._db.execute(
            """INSERT INTO summaries (chat_id, summary, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(chat_id) DO UPDATE SET summary = ?, updated_at = CURRENT_TIMESTAMP""",
            (chat_id, summary, summary)
        )
        await self._db.commit()

    async def get_summary(self, chat_id: int) -> str | None:
        async with self._db.execute(
            "SELECT summary FROM summaries WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row['summary'] if row else None

    async def close(self):
        """Close the database connection."""
        if self._db:
            await self._db.close()
